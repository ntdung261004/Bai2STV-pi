# file: modules/utils.py
import cv2

def apply_digital_zoom(frame, zoom_level):
    """Thực hiện zoom kỹ thuật số và trả về cả khung hình đã zoom lẫn vùng đã crop."""
    if zoom_level <= 1.0 or frame is None:
        return frame, None
    h, w, _ = frame.shape
    crop_w = int(w / zoom_level)
    crop_h = int(h / zoom_level)
    mid_x, mid_y = w // 2, h // 2
    x1 = mid_x - crop_w // 2
    y1 = mid_y - crop_h // 2
    crop_region = (x1, y1, crop_w, crop_h)
    cropped_frame = frame[y1:y1 + crop_h, x1:x1 + crop_w]
    zoomed_frame = cv2.resize(cropped_frame, (w, h), interpolation=cv2.INTER_LINEAR)
    return zoomed_frame, crop_region

def draw_crosshair_on_frame(input_frame, zoom_level, center_point):
    """Vẽ tâm ngắm lên một khung hình dựa trên mức zoom và tọa độ gốc."""
    zoomed_frame, crop_region = apply_digital_zoom(input_frame, zoom_level)
    
    # Tọa độ tâm ngắm gốc
    center_on_original = (center_point['x'], center_point['y'])

    if crop_region: # Trường hợp có zoom
        x1, y1, crop_w, crop_h = crop_region
        cx, cy = center_on_original
        # Chỉ vẽ nếu tâm ngắm nằm trong vùng nhìn thấy
        if x1 <= cx < x1 + crop_w and y1 <= cy < y1 + crop_h:
            draw_x = int((cx - x1) * zoom_level)
            draw_y = int((cy - y1) * zoom_level)
            cv2.drawMarker(zoomed_frame, (draw_x, draw_y), color=(0, 0, 255), markerType=cv2.MARKER_CROSS, markerSize=30, thickness=2)
    else: # Trường hợp không zoom
        cv2.drawMarker(zoomed_frame, center_on_original, color=(0, 0, 255), markerType=cv2.MARKER_CROSS, markerSize=30, thickness=2)
        
    return zoomed_frame