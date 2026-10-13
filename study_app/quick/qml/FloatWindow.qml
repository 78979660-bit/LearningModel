import QtQuick
import QtQuick.Controls
import QtQuick.Window
import "."

Window {
    id: root
    objectName: "floatWindow"
    title: "和纸学习室浮窗"
    width: 56; height: 56
    minimumWidth: 56; minimumHeight: 56
    maximumWidth: 56; maximumHeight: 56
    visible: false
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    property var controller: null
    signal restoreRequested()
    signal quitRequested()

    Rectangle {
        anchors.fill: parent
        anchors.margins: 3
        radius: 15
        color: Theme.paper
        border.color: Theme.line
        Image {
            anchors.centerIn: parent
            width: 36; height: 36
            source: Qt.resolvedUrl("../../ui/assets/app.ico")
            fillMode: Image.PreserveAspectFit
        }
    }
    Item {
        id: restoreControl
        objectName: "FloatRestoreButton"
        anchors.fill: parent
        activeFocusOnTab: true
        Accessible.role: Accessible.Button
        Accessible.name: "双击打开和纸学习室"
        Accessible.onPressAction: root.restoreRequested()
        Keys.onReturnPressed: root.restoreRequested()
        Keys.onEnterPressed: root.restoreRequested()
        MouseArea {
            objectName: "FloatDragArea"
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            property point pressGlobal: Qt.point(0, 0)
            property real pressWindowX: 0
            property real pressWindowY: 0
            onPressed: function(mouse) {
                pressGlobal = mapToGlobal(mouse.x, mouse.y)
                pressWindowX = root.x; pressWindowY = root.y
            }
            onPositionChanged: function(mouse) {
                if (!(pressedButtons & Qt.LeftButton)) return
                const current = mapToGlobal(mouse.x, mouse.y)
                root.x = pressWindowX + current.x - pressGlobal.x
                root.y = pressWindowY + current.y - pressGlobal.y
            }
            onDoubleClicked: function(mouse) { if (mouse.button === Qt.LeftButton) root.restoreRequested() }
            onClicked: function(mouse) { if (mouse.button === Qt.RightButton) actions.popup() }
        }
    }
    Menu {
        id: actions
        MenuItem { text: "打开学习室"; onTriggered: root.restoreRequested() }
        MenuItem { objectName: "FloatQuitButton"; text: "退出应用"; onTriggered: root.quitRequested() }
    }
    Shortcut { sequence: "Escape"; onActivated: root.restoreRequested() }
    onVisibleChanged: if (visible) restoreControl.forceActiveFocus()
}
