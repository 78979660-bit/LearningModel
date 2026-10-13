import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

TextArea {
    id: root
    property string accessibleLabel: placeholderText
    Layout.fillWidth: true
    implicitHeight: Math.max(100, contentHeight + topPadding + bottomPadding)
    color: Theme.ink
    placeholderTextColor: Theme.muted
    font.family: Theme.fontFamily
    font.pixelSize: Theme.bodyFontSize
    wrapMode: TextEdit.Wrap
    textFormat: TextEdit.PlainText
    selectByMouse: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: accessibleLabel
    background: Rectangle {
        radius: Theme.controlRadius
        gradient: Gradient {
            GradientStop { position: 0; color: root.enabled ? Qt.alpha("#FFFEFA", 0.34) : "#F2ECE6" }
            GradientStop { position: 1; color: root.enabled ? Qt.alpha(Theme.paper, 0.12) : "#EFE9E3" }
        }
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? Theme.focusRing : Theme.line
    }
}
