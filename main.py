# file: main.py
import threading
import time
import logging
import queue
import evdev
import socketio
# Import từ các module đã tách
from modules.camera import Camera
from modules.workers import TriggerListener, ProcessingWorker, StreamerWorker, CommandPoller
# Import module âm thanh mới
from modules.audio import audio_player

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
SESSION_DURATION_SECONDS = 115 # Ví dụ: phiên kéo dài 3 phút (180 giây)
session_end_time = None

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

def decrement_bullet():
    """Giảm số đạn đi một và gửi cập nhật về server."""
    global bullet_count
    with session_lock:
        if bullet_count > 0:
            bullet_count -= 1
            logging.info(f"Đạn đã bắn! Số đạn còn lại: {bullet_count}")
            # **SỬA ĐỔI**: Gửi số đạn mới về server
            if sio.connected:
                sio.emit('update_ammo', {'ammo': bullet_count})
            
            if bullet_count == 0:
                logging.info("="*20 + " HẾT ĐẠN " + "="*20)

def start_session():
    """Kích hoạt và reset các thông số cho một phiên bắn mới."""
    global session_active, bullet_count, session_end_time
    with session_lock:
        # Bỏ điều kiện "if not session_active" để lệnh "start" luôn reset lại phiên
        session_active = True
        bullet_count = 16
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

# --- KHỞI CHẠY CHƯƠNG TRÌNH ---
if __name__ == '__main__':
    # **SỬA ĐỔI**: Kết nối SocketIO tới server
    try:
        sio.connect(f"http://{SERVER_IP}:{SERVER_PORT}")
    except socketio.exceptions.ConnectionError as e:
        logging.error(f"Lỗi kết nối SocketIO: {e}. Giao diện sẽ không cập nhật số đạn.")
        
    # THÊM MỚI: Tải file âm thanh khi chương trình bắt đầu
    audio_player.load_sound('shot', 'sounds/shot.mp3')
    logging.info("Chương trình khởi động. Trạng thái phiên bắn: INACTIVE.")
    camera = Camera(width=CAMERA_CAPTURE_WIDTH, height=CAMERA_CAPTURE_HEIGHT).start()
    print(f"Camera đã khởi động ở chế độ {CAMERA_CAPTURE_WIDTH}x{CAMERA_CAPTURE_HEIGHT}.")
    time.sleep(2.0)

    # Khởi tạo các luồng worker (giữ nguyên)
    threads = [
        StreamerWorker(camera, VIDEO_UPLOAD_URL, state_lock, get_current_state, FPS),
        CommandPoller(COMMAND_POLL_URL, set_state_from_command, start_session),
        TriggerListener(TRIGGER_DEVICE_NAME, TRIGGER_KEY_CODE, camera, processing_queue, state_lock, get_current_state, can_fire,decrement_bullet),
        ProcessingWorker(processing_queue)
    ]

    for t in threads:
        t.start()
    
    print("✅ Tất cả các luồng đã được khởi động. Hệ thống đang hoạt động.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Nhận tín hiệu thoát, chương trình sẽ kết thúc.")