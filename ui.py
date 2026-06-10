"""Drawing + input helpers for the FacePay kiosk terminal.

Everything is drawn onto the OpenCV camera frame so the whole thing looks like a
payment-device screen. Colours are BGR (OpenCV order).
"""

import cv2

FONT = cv2.FONT_HERSHEY_SIMPLEX

WHITE = (255, 255, 255)
GREY = (150, 150, 150)
GREEN = (90, 210, 120)
RED = (80, 80, 235)
AMBER = (60, 190, 245)
PANEL = (35, 30, 28)


def darken(frame, alpha=0.6):
    shade = frame.copy()
    shade[:] = (18, 16, 15)
    cv2.addWeighted(shade, alpha, frame, 1 - alpha, 0, frame)
    return frame


def _fit_scale(text, scale, thick, max_w, max_h=None):
    while scale > 0.3:
        w, h = cv2.getTextSize(text, FONT, scale, thick)[0]
        if w <= max_w and (max_h is None or h <= max_h):
            break
        scale -= 0.1
    return scale


def text_center(frame, text, y, scale=1.0, color=WHITE, thick=2):
    scale = _fit_scale(text, scale, thick, int(frame.shape[1] * 0.92))
    size = cv2.getTextSize(text, FONT, scale, thick)[0]
    x = (frame.shape[1] - size[0]) // 2
    cv2.putText(frame, text, (x, y), FONT, scale, color, thick, cv2.LINE_AA)


def button(frame, rect, label, color=WHITE, scale=0.9):
    x, y, w, h = rect
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
    scale = _fit_scale(label, scale, 2, int(w * 0.85), int(h * 0.6))
    size = cv2.getTextSize(label, FONT, scale, 2)[0]
    cv2.putText(frame, label, (x + (w - size[0]) // 2, y + (h + size[1]) // 2),
                FONT, scale, color, 2, cv2.LINE_AA)


def in_rect(point, rect):
    if point is None:
        return False
    px, py = point
    x, y, w, h = rect
    return x <= px <= x + w and y <= py <= y + h


class Mouse:
    """Tracks the last left-click on a window."""

    def __init__(self, window):
        self._point = None
        cv2.setMouseCallback(window, self._on_event)

    def _on_event(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            self._point = (x, y)

    def take(self):
        point, self._point = self._point, None
        return point
