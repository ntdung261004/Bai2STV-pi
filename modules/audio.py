# file: modules/audio_player.py
import pygame

class AudioPlayer:
    def __init__(self):
        self.sounds = {}
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            print("🔊 Khởi tạo Audio Player thành công.")
        except Exception as e:
            print(f"⚠️ Lỗi khởi tạo Audio Player: {e}. Âm thanh có thể sẽ không hoạt động.")

    def load_sound(self, name, path):
        """Tải một file âm thanh và lưu vào bộ nhớ."""
        try:
            sound = pygame.mixer.Sound(path)
            self.sounds[name] = sound
            print(f"🎶 Đã tải thành công âm thanh '{name}' từ {path}")
        except Exception as e:
            print(f"⚠️ Lỗi khi tải âm thanh '{name}': {e}")

    def play(self, name):
        """Phát một âm thanh đã được tải."""
        if name in self.sounds:
            self.sounds[name].play()
        else:
            print(f"⚠️ Không tìm thấy âm thanh có tên '{name}' để phát.")

# Tạo một thực thể duy nhất của AudioPlayer để sử dụng trong toàn bộ chương trình
audio_player = AudioPlayer()