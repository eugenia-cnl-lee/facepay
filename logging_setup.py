"""Silence TensorFlow's noisy startup logs.

Import this module *before* importing deepface or tensorflow. These settings
are read once, at import time, so they must be applied first.
"""

import logging
import os
import warnings

# Hide TensorFlow's C++ INFO/WARNING logs and the oneDNN notice.
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")

# Hide the Python-level deprecation chatter that leaks past the settings above.
warnings.filterwarnings("ignore", category=DeprecationWarning)
logging.getLogger("tensorflow").setLevel(logging.ERROR)
