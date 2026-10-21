import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

ComboBox {
    id: root
    property string accessibleLabel: "选择选项"
    Layout.fillWidth: true
    implicitHeight: 48
    font.family: Theme.fontFamily
    font.pixelSize: Theme.bodyFontSize
    // Basic uses the light palette color behind its highlighted delegate.
    // Keep its label legible instead of drawing paper-colored text on paper.
    palette.highlightedText: Theme.ink
    focusPolicy: Qt.StrongFocus
    Accessible.name: accessibleLabel
    indicator: Text {
        x: root.width - width - root.padding
        y: (root.height - height) / 2
        width: 22
        text: "⌄"
        textFormat: Text.PlainText
        horizontalAlignment: Text.AlignHCenter
        color: root.enabled ? Theme.ink : Theme.muted
        font.family: "Segoe UI"
        font.pixelSize: 22
        Accessible.ignored: true
    }
    background: Rectangle {
        radius: Theme.controlRadius
        gradient: Gradient {
            GradientStop { position: 0; color: root.enabled ? "#FFFEFA" : "#F2ECE6" }
            GradientStop { position: 1; color: root.enabled ? Theme.paper : "#EFE9E3" }
        }
        border.width: parent.activeFocus ? 2 : 1
        border.color: parent.activeFocus ? Theme.focusRing : Theme.line
    }
}
