import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "."

Item {
    id: root
    objectName: "sidebar"
    required property var bridge
    property bool collapsed: bridge ? bridge.sidebarCollapsed : false
    property string currentPage: bridge ? bridge.currentPage : "home"
    property var routes: bridge ? (bridge.routes || []) : []
    clip: true

    implicitWidth: collapsed ? Theme.sidebarCollapsedWidth : Theme.sidebarExpandedWidth
    Layout.minimumWidth: Theme.sidebarCollapsedWidth
    Layout.preferredWidth: implicitWidth
    Layout.maximumWidth: Theme.sidebarExpandedWidth
    Layout.fillHeight: true

    Behavior on Layout.preferredWidth {
        NumberAnimation { duration: 220; easing.type: Easing.OutCubic }
    }

    function iconFor(key) {
        if (key === "graph") return Qt.resolvedUrl("../assets/icons/knowledge.svg")
        return Qt.resolvedUrl("../assets/icons/" + key + ".svg")
    }

    function objectNameFor(key) {
        return "nav-" + (key === "graph" ? "knowledge" : key)
    }

    Rectangle {
        anchors.fill: parent
        color: Theme.sidebar
    }

    Image {
        anchors.fill: parent
        source: Qt.resolvedUrl("../assets/paper-texture.svg")
        sourceSize: Qt.size(960, 960)
        fillMode: Image.Tile
        opacity: 0.74
        smooth: true
        asynchronous: false
    }

    Rectangle {
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: 1
        color: Qt.alpha(Theme.ink, 0.09)
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 8

        Button {
            id: menuToggle
            objectName: "menuToggle"
            Accessible.name: root.collapsed ? "展开菜单" : "收起菜单"
            Layout.preferredWidth: 44
            Layout.minimumWidth: 44
            Layout.maximumWidth: 44
            Layout.preferredHeight: 42
            focusPolicy: Qt.StrongFocus
            onClicked: if (root.bridge) root.bridge.toggleSidebar()
            Keys.onReturnPressed: (event) => { clicked(); event.accepted = true }
            Keys.onEnterPressed: (event) => { clicked(); event.accepted = true }
            background: Rectangle {
                radius: Theme.cardRadius
                color: menuToggle.activeFocus ? Theme.accentSoft : "transparent"
                border.width: menuToggle.activeFocus ? 1 : 0
                border.color: Theme.accent
            }
            contentItem: AppIcon {
                source: Qt.resolvedUrl("../assets/icons/menu.svg")
                iconSize: 23
            }
            ToolTip.visible: hovered && root.collapsed
            ToolTip.text: root.collapsed ? "展开菜单" : "收起菜单"
            ToolTip.delay: 450
        }

        ColumnLayout {
            // Keep navigation rows at the same height while the brand fades.
            opacity: root.collapsed ? 0 : 1
            Behavior on opacity { NumberAnimation { duration: 120 } }
            Layout.fillWidth: true
            Layout.topMargin: 2
            Layout.bottomMargin: 8
            spacing: 4
            Text {
                Accessible.ignored: root.collapsed
                text: "和纸学习室"
                textFormat: Text.PlainText
                color: Theme.ink
                font.family: Theme.headingFontFamily
                font.pixelSize: Theme.sectionFontSize
                font.weight: Font.DemiBold
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
            }
            Text {
                Accessible.ignored: root.collapsed
                text: "Washi Study"
                textFormat: Text.PlainText
                color: Theme.muted
                font.family: Theme.headingFontFamily
                font.pixelSize: Theme.captionFontSize
                font.letterSpacing: 1.1
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
            }
        }

        Rectangle {
            opacity: root.collapsed ? 0 : 1
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: Qt.alpha(Theme.ink, 0.12)
        }

        Repeater {
            model: root.routes
            delegate: Button {
                id: navButton
                required property var modelData
                property var route: modelData
                readonly property bool selected: route.key === root.currentPage
                objectName: root.objectNameFor(route.key)
                Accessible.name: String(route.label || "导航")
                Accessible.role: Accessible.PageTab
                Accessible.selected: selected
                Layout.fillWidth: true
                Layout.preferredHeight: 58
                checkable: false
                focusPolicy: Qt.StrongFocus
                onClicked: if (root.bridge) root.bridge.navigate(String(route.key))
                Keys.onReturnPressed: (event) => { clicked(); event.accepted = true }
                Keys.onEnterPressed: (event) => { clicked(); event.accepted = true }

                background: Rectangle {
                    radius: Theme.controlRadius
                    color: navButton.selected ? Theme.paper
                                               : (navButton.hovered ? Qt.alpha(Theme.paper, 0.55) : "transparent")
                    border.width: navButton.activeFocus ? 2 : navButton.selected ? 1 : 0
                    border.color: navButton.activeFocus ? Theme.focusRing : Qt.alpha("#FFFFFF", 0.78)
                }

                Rectangle {
                    visible: navButton.selected
                    width: 4
                    radius: 2
                    color: Theme.accent
                    anchors.left: parent.left
                    anchors.leftMargin: 1
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    anchors.margins: 7
                }

                contentItem: Item {
                    AppIcon {
                        objectName: "nav-icon-" + navButton.route.key
                        source: root.iconFor(navButton.route.key)
                        iconSize: 21
                        width: iconSize
                        height: iconSize
                        x: root.collapsed ? (parent.width - width) / 2 : 13
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        objectName: "nav-label-" + navButton.route.key
                        opacity: root.collapsed ? 0 : 1
                        Behavior on opacity { NumberAnimation { duration: 120 } }
                        Accessible.ignored: true
                        text: String(navButton.route.label || "")
                        textFormat: Text.PlainText
                        color: Theme.ink
                        font.family: Theme.fontFamily
                        font.pixelSize: 20
                        font.weight: navButton.selected ? Font.DemiBold : Font.Normal
                        elide: Text.ElideRight
                        verticalAlignment: Text.AlignVCenter
                        anchors.left: parent.left
                        anchors.leftMargin: 48
                        anchors.right: parent.right
                        anchors.rightMargin: 8
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                    }
                }

                ToolTip.visible: hovered && root.collapsed
                ToolTip.text: String(route.label || "")
                ToolTip.delay: 450
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 40
            clip: true

            Image {
                id: bamboo
                objectName: "sidebarBamboo"
                visible: parent.height >= 100
                anchors.left: parent.left
                anchors.bottom: parent.bottom
                anchors.leftMargin: -12
                anchors.bottomMargin: -8
                width: Math.min(206, parent.width + 12)
                height: Math.min(parent.height + 8, width * 340 / 280)
                source: Qt.resolvedUrl("../assets/bamboo-leaves.svg")
                sourceSize: Qt.size(560, 680)
                fillMode: Image.PreserveAspectFit
                horizontalAlignment: Image.AlignLeft
                verticalAlignment: Image.AlignBottom
                opacity: root.collapsed ? 0.38 : 0.86
                Behavior on opacity { NumberAnimation { duration: 120 } }
                smooth: true
                asynchronous: false
                transform: Rotation {
                    origin.x: 18
                    origin.y: bamboo.height * 0.97
                    angle: -Theme.bambooSway * 3.2
                }
            }
        }
        ToolButton {
            objectName: "windowActionsButton"
            text: "···"
            font.family: Theme.fontFamily
            font.pixelSize: 24
            Accessible.name: "窗口操作"
            Layout.alignment: Qt.AlignLeft
            onClicked: windowMenu.popup()
            Menu {
                id: windowMenu
                y: -height
                MenuItem {
                    objectName: "ambientWindToggle"
                    text: "动态背景"
                    checkable: true
                    checked: Theme.windEnabled
                    onTriggered: Theme.windEnabled = !Theme.windEnabled
                }
                MenuItem {
                    objectName: "homeHideButton"
                    text: "收起到浮窗"
                    enabled: !!root.bridge && !!root.bridge.lifecycle && root.bridge.lifecycle.active
                    onTriggered: root.bridge.lifecycle.showFloat()
                }
                MenuItem {
                    objectName: "homeQuitButton"
                    text: "退出应用"
                    enabled: !!root.bridge && !!root.bridge.lifecycle && root.bridge.lifecycle.active
                    onTriggered: root.bridge.lifecycle.requestQuit()
                }
            }
        }
    }
}
