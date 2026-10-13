import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Button {
    id: root
    property bool primary: false
    readonly property bool iconOnly: text.length === 0 && iconSource.toString().length > 0
    property url iconSource
    property string accessibleLabel: ""

    implicitWidth: iconOnly ? 44 : Math.max(80, buttonLabel.implicitWidth + (iconSource.toString().length ? 26 : 0) + 28)
    implicitHeight: 48
    padding: iconOnly ? 8 : 12
    focusPolicy: Qt.StrongFocus
    Accessible.name: accessibleLabel.length > 0 ? accessibleLabel : text
    Keys.onReturnPressed: (event) => { if (enabled) clicked(); event.accepted = true }
    Keys.onEnterPressed: (event) => { if (enabled) clicked(); event.accepted = true }

    background: Rectangle {
        visible: !root.iconOnly || root.hovered || root.activeFocus || root.down
        radius: Theme.controlRadius
        gradient: Gradient {
            GradientStop {
                position: 0
                color: root.primary ? (root.down ? Theme.accentPressed : root.hovered ? Theme.accentHover : Qt.lighter(Theme.accent, 1.08))
                                    : (root.down ? Theme.accentSoft : root.hovered ? Theme.pinkSoft : Theme.paper)
            }
            GradientStop {
                position: 1
                color: root.primary ? (root.down ? Theme.accentPressed : root.hovered ? Theme.accentHover : Theme.accent)
                                    : (root.down ? Theme.accentSoft : root.hovered ? Theme.pinkSoft : "#FCF8F2")
            }
        }
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? Theme.focusRing : (root.primary ? Theme.accent : Qt.alpha(Theme.accent, 0.38))
        opacity: root.enabled ? 1 : 0.55

        Image {
            anchors.fill: parent
            anchors.margins: 3
            source: Qt.resolvedUrl("../assets/paper-texture.svg")
            sourceSize: Qt.size(960, 960)
            fillMode: Image.Tile
            opacity: root.primary ? 0.22 : 0.35
        }

        Rectangle {
            x: 1
            y: 1
            width: parent.width - 2
            height: 1
            radius: 1
            color: Qt.alpha("#FFFFFF", root.primary ? 0.28 : 0.72)
        }
    }

    contentItem: Item {
        implicitWidth: buttonLabel.implicitWidth + (buttonIcon.visible ? buttonIcon.width + (buttonLabel.visible ? 8 : 0) : 0)
        implicitHeight: Math.max(buttonLabel.implicitHeight, buttonIcon.height)
        Row {
            anchors.centerIn: parent
            spacing: buttonIcon.visible && buttonLabel.visible ? 8 : 0
            AppIcon {
                id: buttonIcon
                visible: root.iconSource.toString().length > 0
                source: root.iconSource
                iconSize: root.iconOnly ? 22 : 18
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                id: buttonLabel
                visible: root.text.length > 0
                text: root.text
                textFormat: Text.PlainText
                color: root.primary ? "#FFFFFF" : Theme.ink
                font.family: Theme.fontFamily
                font.pixelSize: Theme.bodyFontSize
                font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
}
