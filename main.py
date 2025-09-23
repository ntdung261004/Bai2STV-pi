# file: main.py
import threading
import time
import logging
import queue
import evdev
import socketio
# Import từ các module đã tách
from modules.camera import Camera
from modules.workers import TriggerListener, ProcessingWorker, StreamerWorker, CommandPoller, StatusReporterWorker, SessionMonitorWorker
# Import module âm thanh mới
from modules.audio import audio_player
from typing import Set # Thêm import này
from modules.yolo_predictor import analyze_shot

# --- CẤU HÌNH ---
# ... (Toàn bộ phần cấu hình giữ nguyên)
SERVER_IP = "192.168.1.100"
SERVER_PORT = 5000
VIDEO_UPLOAD_URL = f"http://{SERVER_IP}:{SERVER_PORT}/pi/video_upload"
COMMAND_POLL_URL = f"http://{SERVER_IP}:{SERVER_PORT}/pi/get_command"
FPS = 25
TRIGGER_DEVICE_NAME = "AB Shutter"
TRIGGER_KEY_CODE = evdev.ecodes.KEY_VOLUMEDOWN
CAMERA_CAPTURE_WIDTH = 640
CAMERA_CAPTURE_HEIGHT = 480
FINAL_FRAME_WIDTH = 480
FINAL_FRAME_HEIGHT = 640

# BIẾN TRẠNG THÁI PHIÊN BẮN
# =================================================================
session_lock = threading.Lock() # Lock để bảo vệ các biến này
session_active = False
bullet_count = 0
SESSION_DURATION_SECONDS = 87 # Ví dụ: phiên kéo dài 3 phút (180 giây)
session_end_time = None
hit_targets_session: Set[str] = set()

# --- TRẠNG THÁI TRUNG TÂM ---
# ... (Phần này giữ nguyên)
state_lock = threading.Lock()
calibrated_center = {'x': FINAL_FRAME_WIDTH // 2, 'y': FINAL_FRAME_HEIGHT // 2}
current_zoom = 1.0
processing_queue = queue.Queue(maxsize=30)

# Cấu hình logging cơ bản
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s')
# --- HÀM QUẢN LÝ TRẠNG THÁI ---
# ... (Phần này giữ nguyên)

# THÊM MỚI: CẤU HÌNH SOCKETIO CLIENT
# =================================================================
sio = socketio.Client()

@sio.event
def connect():
    logging.info("✅ Đã kết nối SocketIO tới server!")

@sio.event
def disconnect():
    logging.warning("⚠️ Đã mất kết nối SocketIO tới server.")
# =================================================================

# --- HÀM GỬI TRẠNG THÁI ---
def send_status_update(component, status):
    """Gửi cập nhật trạng thái của một thành phần về server."""
    if sio.connected:
        logging.info(f"Gửi trạng thái: [{component}] -> {status}")
        sio.emit('status_update', {'component': component, 'status': status})

def send_ammo_update(ammo):
    """Gửi số đạn còn lại về server."""
    if sio.connected:
        sio.emit('update_ammo', {'ammo': ammo})
        
def get_current_state():
    return current_zoom, calibrated_center

def set_state_from_command(command):
    global current_zoom, calibrated_center
    command_type = command.get('type')
    command_value = command.get('value')
    with state_lock:
        if command_type == 'zoom':
            current_zoom = float(command_value)
            print(f"--- NHẬN LỆNH ZOOM: {current_zoom}x ---")
        elif command_type == 'center':
            relative_x = float(command_value['x'])
            relative_y = float(command_value['y'])
            zoom = current_zoom
            w, h = FINAL_FRAME_WIDTH, FINAL_FRAME_HEIGHT
            crop_w, crop_h = int(w / zoom), int(h / zoom)
            x1, y1 = (w - crop_w) // 2, (h - crop_h) // 2
            x_in_crop = relative_x * crop_w
            y_in_crop = relative_y * crop_h
            new_absolute_x = int(x1 + x_in_crop)
            new_absolute_y = int(y1 + y_in_crop)
            calibrated_center = {'x': new_absolute_x, 'y': new_absolute_y}
            print(f"--- TÂM MỚI (TRÊN KHUNG HÌNH GỐC): ({new_absolute_x}, {new_absolute_y}) ---")

# THÊM MỚI: HÀM KIỂM TRA ĐIỀU KIỆN BẮN VÀ GIẢM ĐẠN
# =================================================================
def can_fire():
    """Kiểm tra tất cả điều kiện trước khi cho phép bắn."""
    with session_lock:
        if not session_active:
            logging.warning("Bắn bị từ chối: Phiên chưa bắt đầu.")
            return False
        
        if time.time() > session_end_time:
            logging.warning("Bắn bị từ chối: Đã hết thời gian.")
            # Tùy chọn: Tự động kết thúc phiên
            # global session_active
            # if session_active:
            #     session_active = False
            #     logging.info("="*20 + " PHIÊN BẮN KẾT THÚC (HẾT GIỜ) " + "="*20)
            return False
            
        if bullet_count <= 0:
            logging.warning("Bắn bị từ chối: Đã hết đạn.")
            return False
            
        return True

def end_session(reason: str):
    """Kết thúc phiên tập, ghi log, và gửi sự kiện 'session_ended' về server."""
    global session_active, bullet_count # Thêm bullet_count vào global
    with session_lock:
        if session_active:
            # --- SỬA ĐỔI: Tính toán kết quả trước khi kết thúc ---
            shots_fired = 16 - bullet_count
            hit_count = len(hit_targets_session)
            achievement = calculate_achievement(hit_targets_session)

            session_active = False
            logging.info("="*25 + " PHIÊN BẮN ĐÃ KẾT THÚC " + "="*25)
            logging.info(f"-> Lý do: {reason}")
            logging.info(f"-> Kết quả: {hit_count} mục tiêu trúng - Xếp loại: {achievement}")

            if sio.connected:
                sio.emit('session_ended', {
                    'reason': reason,
                    'total_shots': shots_fired,
                    'hit_count': hit_count,
                    'achievement': achievement
                })
                
def get_session_state():
    """Lấy trạng thái (active, end_time, ammo) của phiên bắn một cách an toàn."""
    with session_lock:
        effective_end_time = session_end_time if session_end_time is not None else float('inf')
        # Sửa đổi: Trả về thêm cả bullet_count
        return session_active, effective_end_time, bullet_count
    
def register_hit(target_name: str):
    """
    Ghi nhận một mục tiêu đã bị bắn trúng một cách an toàn (thread-safe).
    Hàm này sẽ được gọi từ ProcessingWorker.
    """
    with session_lock:
        # Chỉ ghi nhận nếu mục tiêu chưa có trong danh sách và phiên đang hoạt động
        if session_active and target_name not in hit_targets_session:
            hit_targets_session.add(target_name)
            logging.info(f"✅ Ghi nhận trúng mục tiêu mới: {target_name}. Tổng số mục tiêu đã trúng: {len(hit_targets_session)}")

            # Gửi cập nhật về giao diện ngay lập tức
            if sio.connected:
                sio.emit('target_hit_update', {'target_name': target_name})

def calculate_achievement(hit_targets: Set[str]):
    """
    Tính toán thành tích dựa trên danh sách các mục tiêu đã trúng.
    """
    hit_count = len(hit_targets)
    has_bia_8c = 'bia_so_8c' in hit_targets

    if hit_count >= 5:
        return "Giỏi"
    if hit_count == 4 and has_bia_8c:
        return "Khá"
    # Trường hợp 4 bia nhưng không có bia 8c, hoặc 3 bia -> Đạt
    if hit_count >= 3:
        return "Đạt"

    return "Không đạt"

def decrement_bullet():
    """Giảm số đạn đi một và gửi cập nhật về server."""
    global bullet_count
    with session_lock:
        if bullet_count > 0:
            bullet_count -= 1
            logging.info(f"Đạn đã bắn! Số đạn còn lại: {bullet_count}")
            send_ammo_update(bullet_count) # Gửi số đạn mới về server

            if bullet_count == 0:
                logging.info("="*20 + " HẾT ĐẠN " + "="*20)

def start_session():
    """Kích hoạt và reset các thông số cho một phiên bắn mới."""
    global session_active, bullet_count, session_end_time
    with session_lock:
        # Bỏ điều kiện "if not session_active" để lệnh "start" luôn reset lại phiên
        session_active = True
        bullet_count = 16
        hit_targets_session.clear()
        session_end_time = time.time() + SESSION_DURATION_SECONDS
        
        logging.info("="*20 + " PHIÊN BẮN MỚI BẮT ĐẦU " + "="*20)
        logging.info(f"-> Số đạn đã nạp: {bullet_count}")
        logging.info(f"-> Phiên sẽ kết thúc lúc: {time.ctime(session_end_time)}")
        
        # Gửi số đạn ban đầu về giao diện
        if sio.connected:
            sio.emit('update_ammo', {'ammo': bullet_count})
            # Phát âm thanh "xuất phát" trên Pi
            #audio_player.play('start_sound') 
# =================================================================
def reset_session():
    """Hủy bỏ phiên tập hiện tại và reset các thông số."""
    global session_active, bullet_count, session_end_time
    with session_lock:
        if session_active:
            session_active = False
            bullet_count = 0
            session_end_time = None
            hit_targets_session.clear()
            logging.info("="*20 + " PHIÊN BẮN ĐÃ ĐƯỢC RESET " + "="*20)
            # Gửi số đạn về 0 để giao diện cập nhật
            send_ammo_update(bullet_count)
            
# --- KHỞI CHẠY CHƯƠNG TRÌNH ---
if __name__ == '__main__':
    try:
        sio.connect(f"http://{SERVER_IP}:{SERVER_PORT}")
    except socketio.exceptions.ConnectionError as e:
        logging.error(f"Lỗi kết nối SocketIO: {e}")

    audio_player.load_sound('shot', 'sounds/shot.mp3')
    camera = Camera(width=CAMERA_CAPTURE_WIDTH, height=CAMERA_CAPTURE_HEIGHT).start()
    logging.info(f"Camera đã khởi động.")
    time.sleep(2.0)

    # === SỬA ĐỔI LỚN: KHỞI TẠO CÁC WORKER ===
    
    # 1. Khởi tạo các worker chính trước
    streamer_worker = StreamerWorker(camera, VIDEO_UPLOAD_URL, state_lock, get_current_state, FPS)
    command_poller = CommandPoller(COMMAND_POLL_URL, set_state_from_command, start_session, reset_session)
    trigger_listener = TriggerListener(
        TRIGGER_DEVICE_NAME, TRIGGER_KEY_CODE, camera, processing_queue, 
        state_lock, get_current_state, can_fire, decrement_bullet
    )
    processing_worker = ProcessingWorker(
        processing_queue, 
        sio, 
        register_hit,
        get_session_state_func=get_session_state,
        end_session_func=end_session
        )
    
    session_monitor = SessionMonitorWorker(session_lock, get_session_state, end_session)
    # 2. Khởi tạo worker giám sát, truyền các worker chính vào cho nó
    status_reporter = StatusReporterWorker(send_status_update, trigger_listener, camera)

    # 3. Gom tất cả vào danh sách để khởi chạy
    threads = [
        streamer_worker,
        command_poller,
        trigger_listener,
        processing_worker,
        status_reporter,
        session_monitor# <-- Worker mới
    ]

    for t in threads:
        t.start()
    
    logging.info("✅ Tất cả các luồng đã được khởi động.")
    
    try:
        # Vòng lặp chính giữ chương trình chạy
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("\n🛑 Nhận tín hiệu thoát, chương trình sẽ kết thúc.")
    finally:
        if sio.connected:
            sio.disconnect()
        camera.stop()