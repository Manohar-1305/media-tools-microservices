import os

SERVICES = {
    "audio-cutter": os.getenv("AUDIO_CUTTER_URL", "http://audio-cutter:5001"),
    "audio-joiner": os.getenv("AUDIO_JOINER_URL", "http://audio-joiner:5002"),
    "video-cutter": os.getenv("VIDEO_CUTTER_URL", "http://video-cutter:5003"),
    "pdf-converter": os.getenv("PDF_CONVERTER_URL", "http://pdf-converter:5004"),
    "pdf-combiner": os.getenv("PDF_COMBINER_URL", "http://pdf-combiner:5005"),
    "user": os.getenv("USER_SERVICE_URL", "http://user:5006"),
    "audio-to-video": os.getenv("AUDIO_TO_VIDEO_URL", "http://audio-to-video:5007"),
    "image-to-pdf": os.getenv("IMAGE_TO_PDF_URL", "http://image-to-pdf:5008"),
    "pdf-to-jpg": os.getenv("PDF_TO_JPG_URL", "http://pdf-to-jpg:5009"),
    "video-subtitle": os.getenv("VIDEO_SUBTITLE_URL", "http://video-subtitle:5010"),
    "word-to-pdf": os.getenv("WORD_TO_PDF_URL", "http://word-to-pdf:5011"),
    "pdf-to-word": os.getenv("PDF_TO_WORD_URL", "http://pdf-to-word:5012"),
    "image-compressor": os.getenv("IMAGE_COMPRESSOR_URL", "http://image-compressor:5013"),
    "video-to-gif": os.getenv("VIDEO_TO_GIF_URL", "http://video-to-gif:5014"),
    "video-to-reels": os.getenv("VIDEO_TO_REELS_URL", "http://video-to-reels:5015"),
}
