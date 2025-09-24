# file: config.py
# -----------------------------------------------------------------------------
# TẬP TIN CẤU HÌNH TRUNG TÂM CHO ỨNG DỤNG TRÊN RASPBERRY PI
# -----------------------------------------------------------------------------
# Thay đổi các giá trị trong file này để tinh chỉnh hoạt động của hệ thống
# mà không cần sửa đổi mã nguồn chính.
# -----------------------------------------------------------------------------

import logging

# --- CẤU HÌNH LOGGING ---
# Mức độ log: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_LEVEL = logging.DEBUG
LOG_FORMAT = '%(asctime)s - %(threadName)s - %(levelname)s - %(message)s'


# --- CẤU HÌNH KẾT NỐI SERVER ---
# Địa chỉ IP của Macbook (máy chủ)
SERVER_IP = "192.168.1.100"
SERVER_PORT = 5000

# URL đầy đủ để giao tiếp với server (tự động tạo, không cần sửa)
BASE_URL = f"http://{SERVER_IP}:{SERVER_PORT}"
VIDEO_UPLOAD_URL = f"{BASE_URL}/pi/video_upload"


# --- CẤU HÌNH CAMERA & VIDEO ---
# Chỉ số của camera (thường là 0 cho camera USB/CSI mặc định)
CAMERA_INDEX = 0
# Tốc độ khung hình (frames per second) mong muốn
FPS = 25
# Độ phân giải gốc khi bắt hình từ camera
CAMERA_CAPTURE_WIDTH = 640
CAMERA_CAPTURE_HEIGHT = 480
# Kích thước khung hình cuối cùng sau khi xoay (cho hợp với màn hình dọc)
FINAL_FRAME_WIDTH = 480
FINAL_FRAME_HEIGHT = 640


# --- CẤU HÌNH THIẾT BỊ BẮN (BLUETOOTH TRIGGER) ---
# Tên chính xác của thiết bị Bluetooth Remote Shutter
# !! QUAN TRỌNG: Đã cập nhật theo yêu cầu của bạn !!
TRIGGER_DEVICE_NAME = "AB Shutter"
# Tên mã phím của nút bấm. "KEY_VOLUMEDOWN" là phổ biến nhất.
# Sử dụng tên thay vì mã số để dễ đọc và tương thích nhiều thiết bị.
TRIGGER_KEY_CODE_NAME = "KEY_VOLUMEDOWN"


# --- CẤU HÌNH PHIÊN BẮN ---
# Tổng thời gian cho một phiên bắn (tính bằng giây)
SESSION_DURATION_SECONDS = 87
# Tổng số đạn được nạp cho mỗi phiên
# !! QUAN TRỌNG: Đã cập nhật theo yêu cầu của bạn !!
TOTAL_AMMO = 16


# --- CẤU HÌNH MÔ HÌNH AI (YOLO) ---
# Đường dẫn tới file model đã huấn luyện.
# File này phải nằm cùng cấp với thư mục `main.py`.
YOLO_MODEL_PATH = 'my_model_bai2v1.pt'


# --- CẤU HÌNH ÂM THANH ---
# Đường dẫn tới file âm thanh tiếng súng
SHOT_SOUND_PATH = 'sounds/shot.mp3'