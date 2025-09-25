# file: main.py (phiên bản cuối cùng, sửa lỗi thoát an toàn triệt để)
import threading
import time
import logging
import queue
import evdev
import socketio
import sys
from typing import Set

import config
from modules.camera import Camera
from modules.workers import (
    TriggerListener, ProcessingWorker, StreamerWorker, 
    CommandPoller, StatusReporterWorker, SessionMonitorWorker
)
from modules.audio import audio_player

# Thiết lập logging (giữ nguyên từ file của bạn)
logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT, force=True)
logging.getLogger("socketio").setLevel(logging.WARNING)
logging.getLogger("engineio").setLevel(logging.WARNING)


class ShootingRangeApp:
    def __init__(self):
        # --- Quản lý Trạng thái (giữ nguyên) ---
        self.state_lock = threading.Lock()
        self.calibrated_center = {'x': config.FINAL_FRAME_WIDTH // 2, 'y': config.FINAL_FRAME_HEIGHT // 2}
        self.current_zoom = 1.0

        self.session_lock = threading.Lock()
        self.session_active = False
        self.bullet_count = 0
        self.session_end_time = None
        self.hit_targets_session: Set[str] = set()

        self.processing_queue = queue.Queue(maxsize=30)
        self.stop_event = threading.Event()

        # --- Các thành phần (Components) ---
        self.sio = socketio.Client(reconnection=False, logger=False) 
        self.camera = Camera(src=config.CAMERA_INDEX, width=config.CAMERA_CAPTURE_WIDTH, height=config.CAMERA_CAPTURE_HEIGHT)
        self.trigger_key_code = self._get_trigger_keycode()
        
        self.video_upload_url = config.VIDEO_UPLOAD_URL
        self.command_poll_url = config.COMMAND_POLL_URL
        self.fps = config.FPS

        # **SỬA LỖI**: Lưu lại tham chiếu đến các luồng để join() sau này
        self.threads = []
        self.connection_thread = None


    # --- Toàn bộ các phương thức quản lý trạng thái (từ _get_trigger_keycode đến is_stopping) ---
    # --- ĐỀU ĐƯỢỢC GIỮ NGUYÊN SO VỚI FILE GỐC CỦA BẠN. ---
    
    def _get_trigger_keycode(self):
        try: return getattr(evdev.ecodes, config.TRIGGER_KEY_CODE_NAME)
        except AttributeError:
            logging.critical(f"❌ LỖI: Tên mã phím '{config.TRIGGER_KEY_CODE_NAME}' trong config.py không hợp lệ!")
            sys.exit(1)

    def get_current_state(self):
        with self.state_lock: return self.current_zoom, self.calibrated_center.copy()

    def set_state_from_command(self, command):
        command_type, value = command.get('type'), command.get('value')
        with self.state_lock:
            if command_type == 'zoom':
                self.current_zoom = float(value); logging.info(f"Lệnh ZOOM: {self.current_zoom}x")
            elif command_type == 'center':
                w, h = config.FINAL_FRAME_WIDTH, config.FINAL_FRAME_HEIGHT
                crop_w, crop_h = int(w / self.current_zoom), int(h / self.current_zoom)
                x1, y1 = (w - crop_w) // 2, (h - crop_h) // 2
                self.calibrated_center['x'] = int(x1 + float(value['x']) * crop_w)
                self.calibrated_center['y'] = int(y1 + float(value['y']) * crop_h)
                logging.info(f"Tâm ngắm mới: {self.calibrated_center}")

    def start_session(self):
        with self.session_lock:
            self.session_active = True; self.bullet_count = config.TOTAL_AMMO
            self.hit_targets_session.clear(); self.session_end_time = time.time() + config.SESSION_DURATION_SECONDS
            logging.info("="*20 + " PHIÊN BẮN MỚI BẮT ĐẦU " + "="*20)
            if self.sio.connected: self.sio.emit('update_ammo', {'ammo': self.bullet_count})

    def reset_session(self):
        with self.session_lock:
            if self.session_active:
                self.session_active = False; self.bullet_count = 0; self.session_end_time = None
                self.hit_targets_session.clear(); logging.info("="*20 + " PHIÊN BẮN ĐÃ ĐƯỢC RESET " + "="*20)
                if self.sio.connected: self.sio.emit('update_ammo', {'ammo': self.bullet_count})
    
    def end_session(self, reason: str):
        with self.session_lock:
            if self.session_active:
                shots_fired = config.TOTAL_AMMO - self.bullet_count; hit_count = len(self.hit_targets_session)
                achievement = self.calculate_achievement(self.hit_targets_session); self.session_active = False
                logging.info("="*25 + " PHIÊN BẮN ĐÃ KẾT THÚC " + "="*25)
                if self.sio.connected: self.sio.emit('session_ended', {
                    'reason': reason, 'total_shots': shots_fired, 'hit_count': hit_count, 'achievement': achievement
                })

    def can_fire(self):
        with self.session_lock:
            return self.session_active and self.bullet_count > 0 and (self.session_end_time is None or time.time() <= self.session_end_time)

    def decrement_bullet(self):
        with self.session_lock:
            if self.bullet_count > 0:
                self.bullet_count -= 1; logging.info(f"Đạn đã bắn! Còn lại: {self.bullet_count}")
                if self.sio.connected: self.sio.emit('update_ammo', {'ammo': self.bullet_count})

    def register_hit(self, target_name: str):
        with self.session_lock:
            if self.session_active and target_name not in self.hit_targets_session:
                self.hit_targets_session.add(target_name); logging.info(f"✅ Ghi nhận trúng mục tiêu: {target_name}")
                if self.sio.connected: self.sio.emit('target_hit_update', {'target_name': target_name})
    
    def get_session_state(self):
        with self.session_lock: return self.session_active, self.session_end_time, self.bullet_count

    def calculate_achievement(self, hit_targets: Set[str]):
        hit_count = len(hit_targets); has_bia_8c = 'bia_so_8c' in hit_targets
        if hit_count >= 5: return "Giỏi"
        if hit_count == 4 and has_bia_8c: return "Khá"
        if hit_count >= 3: return "Đạt"
        return "Không đạt"
        
    def send_status_update(self, component, status):
        if self.sio.connected: self.sio.emit('status_update', {'component': component, 'status': status})

    def is_stopping(self):
        return self.stop_event.is_set()

    # --- TÍCH HỢP MỚI: Logic quản lý kết nối tự động (đã sửa lỗi) ---

    def _setup_socketio_events(self):
        @self.sio.event
        def connect(): logging.info(f"✅ Kết nối Socket.IO thành công tới server (SID: {self.sio.sid})")
        @self.sio.event
        def disconnect(): logging.warning("⚠️ Đã mất kết nối Socket.IO tới server.")
    
    def _connection_manager(self):
        logging.info("Luồng Quản lý Kết nối bắt đầu hoạt động.")
        while not self.is_stopping():
            if not self.sio.connected:
                try:
                    logging.info(f"Đang thử kết nối tới server tại {config.BASE_URL}...")
                    self.sio.connect(config.BASE_URL, transports=['websocket'])
                except Exception:
                    # **SỬA LỖI**: Dùng wait() để có thể bị ngắt ngay khi có tín hiệu dừng
                    if self.stop_event.wait(5): # Chờ 5 giây
                        break # Nếu stop_event được set trong lúc chờ, thoát ngay vòng lặp
            else:
                self.sio.wait()
        logging.info("Luồng Quản lý Kết nối đã dừng.")
    
    # --- Cập nhật phương thức điều khiển chính ---
    
    def run(self):
        logging.info("🚀 Khởi động ứng dụng...")
        self.stop_event.clear()

        audio_player.load_sound('shot', config.SHOT_SOUND_PATH)
        self._setup_socketio_events()
        
        self.connection_thread = threading.Thread(target=self._connection_manager, name="_connection_manager", daemon=True)
        self.connection_thread.start()

        self.camera.start()
        logging.info("Vui lòng chờ camera và kết nối ổn định...")
        time.sleep(2.0)

        trigger_listener = TriggerListener(self, config.TRIGGER_DEVICE_NAME, self.trigger_key_code)
        self.threads = [
            StreamerWorker(self), CommandPoller(self), trigger_listener,
            ProcessingWorker(self), SessionMonitorWorker(self),
            StatusReporterWorker(self, trigger_listener, self.camera)
        ]
        for t in self.threads: t.start()
        
        logging.info("✅ Tất cả các luồng nghiệp vụ đã được khởi động. Hệ thống sẵn sàng.")
        
        try:
            # **SỬA LỖI**: Vòng lặp chính chỉ cần giữ cho chương trình sống và chờ tín hiệu dừng
            while not self.is_stopping():
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("\n🛑 Nhận tín hiệu Ctrl+C.")
        finally:
            # **SỬA LỖI QUAN TRỌNG**: Gọi shutdown() ở đây để đảm bảo nó luôn được thực thi
            # Chỉ gọi một lần duy nhất
            if not self.is_stopping():
                self.shutdown()

    def shutdown(self):
        logging.info("... Đang dừng ứng dụng ...")
        
        # 1. Gửi tín hiệu dừng cho TẤT CẢ các luồng
        self.stop_event.set()
        
        # 2. Ngắt kết nối socket một cách chủ động
        #    Điều này sẽ làm cho sio.wait() hoặc stop_event.wait() thoát ra ngay lập tức
        if self.sio.connected:
            self.sio.disconnect()
            
        # 3. Chờ luồng quản lý kết nối kết thúc
        if self.connection_thread and self.connection_thread.is_alive():
            self.connection_thread.join()
            
        # 4. Dừng camera
        self.camera.stop()
        
        logging.info("✅ Ứng dụng đã dừng hoàn toàn.")


if __name__ == '__main__':
    app = ShootingRangeApp()
    # **SỬA LỖI**: Bọc hàm run trong try...finally để đảm bảo shutdown được gọi
    try:
        app.run()
    except KeyboardInterrupt:
        # Khối này thực ra không cần thiết vì run() đã xử lý, nhưng để đây cho chắc chắn
        pass 
    finally:
        if not app.is_stopping():
             app.shutdown()