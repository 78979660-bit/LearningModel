import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

TextField {
    id: root
    property string accessibleLabel: placeholderText
    Layout.fillWidth: true
    implicitHeight: 48
    color: Theme.ink
    placeholderTextColor: Theme.muted
    font.family: Theme.fontFamily
    font.pixelSize: Theme.bodyFontSize
    verticalAlignment: TextInput.AlignVCenter
    selectByMouse: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: accessibleLabel
    background: Rectangle {
        radius: Theme.controlRadius
        gradient: Gradient {
            GradientStop { position: 0; color: root.enabled ? "#FFFEFA" : "#F2ECE6" }
            GradientStop { position: 1; color: root.enabled ? Theme.paper : "#EFE9E3" }
        }
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? Theme.focusRing : Theme.line
    }
}
