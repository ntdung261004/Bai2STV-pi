# file: main.py
import threading
import time
import queue
import evdev

# Import từ các module đã tách
from modules.camera import Camera
from modules.workers import TriggerListener, ProcessingWorker, StreamerWorker, CommandPoller
# Import module âm thanh mới
from modules.audio import audio_player

# --- CẤU HÌNH ---
# ... (Toàn bộ phần cấu hình giữ nguyên)
SERVER_IP = "192.168.1.100"
SERVER_PORT = 5000
VIDEO_UPLOAD_URL = f"http://{SERVER_IP}:{SERVER_PORT}/video_upload"
COMMAND_POLL_URL = f"http://{SERVER_IP}:{SERVER_PORT}/get_command"
FPS = 25
TRIGGER_DEVICE_NAME = "AB Shutter"
TRIGGER_KEY_CODE = evdev.ecodes.KEY_VOLUMEDOWN
CAMERA_CAPTURE_WIDTH = 640
CAMERA_CAPTURE_HEIGHT = 480
FINAL_FRAME_WIDTH = 480
FINAL_FRAME_HEIGHT = 640

# --- TRẠNG THÁI TRUNG TÂM ---
# ... (Phần này giữ nguyên)
state_lock = threading.Lock()
calibrated_center = {'x': FINAL_FRAME_WIDTH // 2, 'y': FINAL_FRAME_HEIGHT // 2}
current_zoom = 1.0
processing_queue = queue.Queue(maxsize=30)

# --- HÀM QUẢN LÝ TRẠNG THÁI ---
# ... (Phần này giữ nguyên)
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

# --- KHỞI CHẠY CHƯƠNG TRÌNH ---
if __name__ == '__main__':
    # THÊM MỚI: Tải file âm thanh khi chương trình bắt đầu
    audio_player.load_sound('shot', 'sounds/shot.mp3')

    camera = Camera(width=CAMERA_CAPTURE_WIDTH, height=CAMERA_CAPTURE_HEIGHT).start()
    print(f"Camera đã khởi động ở chế độ {CAMERA_CAPTURE_WIDTH}x{CAMERA_CAPTURE_HEIGHT}.")
    time.sleep(2.0)

    # Khởi tạo các luồng worker (giữ nguyên)
    threads = [
        StreamerWorker(camera, VIDEO_UPLOAD_URL, state_lock, get_current_state, FPS),
        CommandPoller(COMMAND_POLL_URL, set_state_from_command),
        TriggerListener(TRIGGER_DEVICE_NAME, TRIGGER_KEY_CODE, camera, processing_queue, state_lock, get_current_state),
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