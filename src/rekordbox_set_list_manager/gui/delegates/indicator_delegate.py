"""Indicator delegate — paints compat/dup indicator dots left of cell text."""

from typing import ClassVar

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPointF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from rekordbox_set_list_manager.models.enums import KeyRelation
from rekordbox_set_list_manager.models.harmonic import ENERGY_RELATIONS, SAFE_RELATIONS

_DOT_MAX = 10  # px diameter cap — also bounded by row height minus padding
_DOT_LEFT = 4  # px gap between the cell's left edge and the indicator
_TEXT_OFFSET = 16  # px text shift when an indicator is present
_BORDER_FACTOR = 120  # QColor.darker() factor for the 1px dot border
_DUP_HEX = "#b4befe"  # lavender — cross-section duplicate marker


class IndicatorDelegate(QStyledItemDelegate):
    """Overlay indicator dots on top of the style-drawn cell.

    Reads ``Qt.ItemDataRole.UserRole`` from the index: a ``KeyRelation``
    tier paints a compatibility dot (Key column — green for mix-safe
    transitions, amber for energy moves); ``True`` paints a lavender
    duplicate marker (Title column).  Any other payload falls back to the
    default delegate paint.  The cell chrome (background, selection,
    focus) is always drawn by the style so QSS theming survives, and the
    text is drawn via ``drawItemText`` so alignment/elision follow the
    model's ``TextAlignmentRole``.
    """

    # Tier sets come from models.harmonic so the classification has a
    # single source of truth; this map only assigns the badge colours.
    _TIER_HEX: ClassVar[dict[KeyRelation, str]] = {
        **dict.fromkeys(SAFE_RELATIONS, "#a6e3a1"),
        **dict.fromkeys(ENERGY_RELATIONS, "#f9e2af"),
    }

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        """Paint the cell, overlaying an indicator dot when a payload exists."""
        payload = index.data(Qt.ItemDataRole.UserRole)
        is_compat = isinstance(payload, KeyRelation)
        if not is_compat and payload is not True:
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        text = opt.text
        opt.text = ""  # style paints background/selection/focus, not the text
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        dot = max(0, min(_DOT_MAX, opt.rect.height() - 8))
        cx = opt.rect.left() + _DOT_LEFT + dot / 2
        cy = opt.rect.center().y()
        fill = QColor(self._TIER_HEX[payload] if is_compat else _DUP_HEX)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(fill.darker(_BORDER_FACTOR), 1))
        painter.setBrush(fill)
        if is_compat:
            painter.drawEllipse(QPointF(cx, cy), dot / 2, dot / 2)
        else:
            half = dot / 2
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(cx, cy - half),
                        QPointF(cx + half, cy),
                        QPointF(cx, cy + half),
                        QPointF(cx - half, cy),
                    ]
                )
            )
        painter.restore()

        text_rect = opt.rect.adjusted(_TEXT_OFFSET, 0, 0, 0)
        color_role = (
            QPalette.ColorRole.HighlightedText
            if opt.state & QStyle.StateFlag.State_Selected
            else QPalette.ColorRole.Text
        )
        elided = opt.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, text_rect.width())
        style.drawItemText(
            painter,
            text_rect,
            int(opt.displayAlignment),
            opt.palette,
            True,
            elided,
            color_role,
        )

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        """Defer to the base delegate so QSS and row heights stay in charge."""
        return super().sizeHint(option, index)
