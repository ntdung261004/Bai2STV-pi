import logging
from ultralytics import YOLO

# --- CẤU HÌNH ---
# Đường dẫn tới file model của bạn. File này phải nằm ở thư mục gốc của dự án.
MODEL_PATH = 'my_model_bai2v1.pt'

# --- TẢI MÔ HÌNH ---
# Tải mô hình một lần duy nhất khi module được import để tối ưu hiệu suất.
# Sử dụng try-except để bắt lỗi nếu không tìm thấy file model.
try:
    logging.info(f"Đang tải mô hình YOLO từ: {MODEL_PATH}...")
    # 'cpu' được chỉ định rõ ràng vì Raspberry Pi không có GPU.
    MODEL = YOLO(MODEL_PATH)
    logging.info("✅ Tải mô hình YOLO thành công!")
except Exception as e:
    logging.error(f"❌ LỖI: Không thể tải file mô hình tại '{MODEL_PATH}'. Chi tiết: {e}")
    MODEL = None

def analyze_shot(frame, center_point):
    """
    Phân tích một khung hình để xác định xem phát bắn có trúng mục tiêu không.

    Args:
        frame (numpy.ndarray): Khung hình (ảnh) được chụp tại thời điểm bắn.
        center_point (dict): Tọa độ tâm ngắm, ví dụ: {'x': 320, 'y': 240}.

    Returns:
        str: Tên của class mục tiêu (vd: 'bia_so_5') nếu trúng.
        None: Nếu không trúng bất kỳ mục tiêu nào.
    """
    if MODEL is None:
        logging.warning("Mô hình YOLO chưa được tải, không thể phân tích.")
        return None

    try:
        # Thực hiện dự đoán trên khung hình
        # verbose=False để không in ra quá nhiều log không cần thiết
        results = MODEL.predict(frame, verbose=False)

        # results là một danh sách, ta lấy kết quả đầu tiên
        result = results[0]
        
        # Lấy tọa độ tâm ngắm
        center_x = center_point['x']
        center_y = center_point['y']

        # Duyệt qua tất cả các bounding box mà mô hình nhận diện được
        for box in result.boxes:
            # Lấy tọa độ của bounding box (x1, y1, x2, y2)
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            
            # --- ĐIỀU KIỆN QUAN TRỌNG NHẤT: KIỂM TRA TÂM NGẮM ---
            # Kiểm tra xem tọa độ tâm ngắm có nằm TRONG bounding box không
            if x1 <= center_x <= x2 and y1 <= center_y <= y2:
                # Lấy tên của class đã trúng
                class_id = int(box.cls[0])
                class_name = result.names[class_id]
                
                logging.info(f"🎯 PHÁT HIỆN TRÚNG MỤC TIÊU: {class_name.upper()}")
                return class_name # Trả về tên mục tiêu và kết thúc hàm

    except Exception as e:
        logging.error(f"Lỗi xảy ra trong quá trình dự đoán của YOLO: {e}")
        return None
        
    # Nếu vòng lặp kết thúc mà không tìm thấy phát bắn trúng nào
    logging.info("-- Phát bắn không trúng mục tiêu nào.--")
    return None