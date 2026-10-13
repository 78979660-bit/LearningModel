import QtQuick
import ".."

Rectangle {
    id: root
    property string text: ""
    property color tone: Theme.accent
    implicitWidth: label.implicitWidth + 20
    implicitHeight: 27
    radius: height / 2
    color: Qt.alpha(tone, 0.12)
    border.width: 1
    border.color: Qt.alpha(tone, 0.15)

    Text {
        id: label
        anchors.centerIn: parent
        text: root.text
        textFormat: Text.PlainText
        color: root.tone
        font.family: Theme.fontFamily
        font.pixelSize: Theme.captionFontSize
        font.weight: Font.DemiBold
    }
}
