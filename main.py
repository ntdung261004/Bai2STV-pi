# file: main.py (Phiên bản đã loại bỏ CommandPoller)

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
# **THAY ĐỔI 1**: Xóa CommandPoller khỏi danh sách import
from modules.workers import (
    TriggerListener, ProcessingWorker, StreamerWorker,
    StatusReporterWorker, SessionMonitorWorker
)
from modules.audio import audio_player

# Thiết lập logging
logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT, force=True)
logging.getLogger("socketio").setLevel(logging.WARNING)
logging.getLogger("engineio").setLevel(logging.WARNING)

class ShootingRangeApp:
    def __init__(self):
        # --- Quản lý Trạng thái ---
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
        self.sio = socketio.Client(reconnection=True, reconnection_delay=5)
        # **THAY ĐỔI 3**: Gọi hàm thiết lập trình lắng nghe sự kiện
        self.setup_socketio_events()
        
        self.camera = Camera(
            src=config.CAMERA_INDEX,
            width=config.CAMERA_CAPTURE_WIDTH,
            height=config.CAMERA_CAPTURE_HEIGHT
        )
        self.trigger_key_code = self._get_trigger_keycode()

        # **THAY ĐỔI 2**: Xóa thuộc tính không còn cần thiết
        self.video_upload_url = config.VIDEO_UPLOAD_URL
        # self.command_poll_url = config.COMMAND_POLL_URL # Đã xóa
        self.fps = config.FPS

        self.workers = []

    def _get_trigger_keycode(self):
        try:
            return getattr(evdev.ecodes, config.TRIGGER_KEY_CODE_NAME)
        except AttributeError:
            logging.critical(f"❌ LỖI: Tên mã phím '{config.TRIGGER_KEY_CODE_NAME}' trong config.py không hợp lệ!")
            sys.exit(1)

    # **THAY ĐỔI 3**: Bổ sung hàm thiết lập trình lắng nghe SocketIO
    def setup_socketio_events(self):
        @self.sio.event
        def connect():
            logging.info("✅ Đã kết nối SocketIO tới server!")

        @self.sio.event
        def disconnect():
            logging.warning("⚠️ Đã mất kết nối SocketIO.")

        @self.sio.on('command_to_pi')
        def handle_command(data):
            """
            Hàm này thay thế hoàn toàn cho CommandPoller.
            Nhận lệnh trực tiếp từ server và thực thi.
            """
            logging.info(f"📬 Nhận được lệnh từ server: {data}")
            command_type = data.get('type')
            if command_type == 'start':
                self.start_session()
            elif command_type == 'reset':
                self.reset_session()
            else:
                self.set_state_from_command(data)

    # --- Các phương thức được gọi bởi Workers (Không thay đổi) ---
    def get_current_state(self):
        with self.state_lock:
            return self.current_zoom, self.calibrated_center.copy()
            
    def send_status_update(self, component, status):
        if self.sio.connected:
            self.sio.emit('status_update', {'component': component, 'status': status})

    def get_session_state(self):
        with self.session_lock:
            return self.session_active, self.session_end_time, self.bullet_count

    def set_state_from_command(self, command):
        command_type = command.get('type')
        value = command.get('value')
        with self.state_lock:
            if command_type == 'zoom': self.current_zoom = float(value)
            elif command_type == 'center':
                w, h = config.FINAL_FRAME_WIDTH, config.FINAL_FRAME_HEIGHT
                crop_w = int(w / self.current_zoom)
                crop_h = int(h / self.current_zoom)
                x1, y1 = (w - crop_w) // 2, (h - crop_h) // 2
                self.calibrated_center['x'] = int(x1 + float(value['x']) * crop_w)
                self.calibrated_center['y'] = int(y1 + float(value['y']) * crop_h)

    def start_session(self):
        with self.session_lock:
            if not self.session_active:
                self.session_active = True
                self.bullet_count = config.TOTAL_AMMO
                self.hit_targets_session.clear()
                self.session_end_time = time.time() + config.SESSION_DURATION_SECONDS
                logging.info("=" * 20 + " PHIÊN BẮN MỚI BẮT ĐẦU " + "=" * 20)
                if self.sio.connected: self.sio.emit('update_ammo', {'ammo': self.bullet_count})

    def reset_session(self):
        with self.session_lock:
            self.session_active = False
            self.bullet_count = 0
            self.session_end_time = None
            self.hit_targets_session.clear()
            logging.info("=" * 20 + " PHIÊN BẮN ĐÃ ĐƯỢC RESET " + "=" * 20)
            if self.sio.connected: self.sio.emit('update_ammo', {'ammo': self.bullet_count})

    def end_session(self, reason: str):
        with self.session_lock:
            if not self.session_active: return
            hit_count = len(self.hit_targets_session)
            achievement = self.calculate_achievement(self.hit_targets_session)
            self.session_active = False
            logging.info("=" * 25 + " PHIÊN BẮN ĐÃ KẾT THÚC " + "=" * 25)
            if self.sio.connected: self.sio.emit('session_ended', {'reason': reason, 'hit_count': hit_count, 'achievement': achievement})

    def can_fire(self):
        with self.session_lock:
            return self.session_active and self.bullet_count > 0

    def decrement_bullet(self):
        with self.session_lock:
            if self.bullet_count > 0:
                self.bullet_count -= 1
                if self.sio.connected: self.sio.emit('update_ammo', {'ammo': self.bullet_count})

    def register_hit(self, target_name: str):
        with self.session_lock:
            if self.session_active and target_name not in self.hit_targets_session:
                self.hit_targets_session.add(target_name)
                if self.sio.connected: self.sio.emit('target_hit_update', {'target_name': target_name})

    def calculate_achievement(self, hit_targets: Set[str]):
        hit_count = len(hit_targets)
        has_bia_8c = any('bia_so_8c' in target for target in hit_targets)
        if hit_count >= 5: return "Giỏi"
        if hit_count == 4 and has_bia_8c: return "Khá"
        if hit_count >= 3: return "Đạt"
        return "Không đạt"
        
    def is_stopping(self):
        return self.stop_event.is_set()

    def connect_to_server(self):
        if not self.sio.connected:
            try:
                self.sio.connect(config.BASE_URL)
            except Exception as e:
                logging.error(f"Không thể kết nối tới server: {e}")

    # --- Phương thức điều khiển chính ---
    def run(self):
        audio_player.load_sound('shot', config.SHOT_SOUND_PATH)
        logging.info("🚀 Khởi động ứng dụng...")
        try:
            self.connect_to_server()
            self.camera.start()
            
            trigger_listener = TriggerListener(
                self, 
                config.TRIGGER_DEVICE_NAME, 
                self.trigger_key_code
            )
            
            # **THAY ĐỔI 1**: Xóa CommandPoller khỏi danh sách workers
            self.workers = [
                StreamerWorker(self),
                # CommandPoller(self), # Đã xóa
                trigger_listener,
                ProcessingWorker(self),
                SessionMonitorWorker(self),
                StatusReporterWorker(self, trigger_listener, self.camera)
            ]
            for t in self.workers:
                t.start()
            
            logging.info("✅ Hệ thống sẵn sàng.")
            
            # **THAY ĐỔI 4**: Dùng sio.wait() để giữ chương trình chạy và lắng nghe sự kiện
            self.sio.wait()

        finally:
            logging.info("\n--- Bắt đầu quy trình tắt ứng dụng an toàn ---")
            self.shutdown()

    def shutdown(self):
        if self.is_stopping():
            return
            
        logging.info("... Đang gửi tín hiệu dừng cho các luồng...")
        self.stop_event.set()
        
        if self.sio.connected:
            logging.info("... Đang ngắt kết nối SocketIO...")
            self.sio.disconnect()
            
        logging.info("... Đang dừng camera...")
        self.camera.stop()
        
        for worker in self.workers:
            if worker.is_alive():
                worker.join(timeout=1)

        logging.info("✅ Ứng dụng đã dừng hoàn toàn.")


if __name__ == '__main__':
    app = ShootingRangeApp()
    try:
        app.run()
    except KeyboardInterrupt:
        pass