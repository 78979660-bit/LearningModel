import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Shapes
import "."
import "components"
import "pages"

ApplicationWindow {
    id: root
    objectName: "mainWindow"
    required property var bridge
    property bool openRecordsAfterNavigation: false
    property string pendingRecordNote: ""
    readonly property var contentLoader: pageLoader

    Binding {
        target: Theme
        property: "windActive"
        value: Theme.windEnabled && root.visible && root.active
               && root.visibility !== Window.Minimized && !startupPreloader.running
    }
    NumberAnimation {
        objectName: "ambientWindClock"
        target: Theme
        property: "windPhase"
        from: 0; to: 1
        duration: 64000
        loops: Animation.Infinite
        running: true
        paused: !Theme.windActive
    }

    width: 1280
    height: 820
    minimumWidth: 800
    minimumHeight: 600
    visible: true
    title: "和纸学习室"
    color: Theme.background
    font.family: Theme.fontFamily
    font.pixelSize: Theme.bodyFontSize
    palette.window: Theme.background
    palette.windowText: Theme.ink
    palette.base: Theme.paper
    palette.text: Theme.ink
    palette.button: Theme.paper
    palette.buttonText: Theme.ink
    palette.highlight: Theme.accent
    palette.highlightedText: Theme.paper
    palette.dark: Theme.line
    palette.mid: Theme.line
    palette.light: Theme.paper

    function openRecords(note) {
        if (!root.bridge) return
        root.pendingRecordNote = String(note || "")
        root.openRecordsAfterNavigation = true
        root.bridge.navigate("plan")
        root.applyPendingRecordsTab()
        if (root.openRecordsAfterNavigation) recordsTabTimer.restart()
    }

    function applyPendingRecordsTab() {
        if (!root.openRecordsAfterNavigation || !root.bridge || root.bridge.currentPage !== "plan") return
        if (!retainedPlan.item) return
        retainedPlan.item.section = 1
        retainedPlan.item.applyRecordNote(root.pendingRecordNote)
        root.pendingRecordNote = ""
        root.openRecordsAfterNavigation = false
    }

    Timer {
        id: recordsTabTimer
        interval: 1
        repeat: false
        onTriggered: root.applyPendingRecordsTab()
    }

    background: Rectangle {
        anchors.fill: parent
        color: Theme.background
        gradient: Gradient {
            GradientStop { position: 0; color: "#F8F2EC" }
            GradientStop { position: 0.52; color: Theme.background }
            GradientStop { position: 1; color: "#F7F0E9" }
        }
        Image {
            anchors.fill: parent
            source: Qt.resolvedUrl("assets/paper-texture.svg")
            sourceSize: Qt.size(960, 960)
            fillMode: Image.Tile
            opacity: 0.80
            smooth: true
            asynchronous: false
        }
    }

    FloatWindow { controller: root.bridge ? root.bridge.lifecycle : null }

    Image {
        id: sakura
        objectName: "sakuraArtwork"
        anchors.top: parent.top
        anchors.right: parent.right
        width: Math.min(640, parent.width * 0.52)
        height: 280
        source: Qt.resolvedUrl("assets/sakura-corner.png")
        fillMode: Image.PreserveAspectFit
        horizontalAlignment: Image.AlignRight
        verticalAlignment: Image.AlignTop
        opacity: root.width < 1000 ? 0.22 : 0.86
        smooth: true
        asynchronous: false
        transform: [Translate { x: -Theme.windSway * 3 }, Rotation {
            origin.x: sakura.width * 0.97
            origin.y: 0
            angle: Theme.windSway * 2.0
        }]
    }

    Component {
        id: windPetalComponent
        Shape {
            required property int index
            readonly property bool headerOnly: parent.objectName === "windHeaderPetals"
            readonly property int seed: index + (headerOnly ? 31 : 0)
            readonly property real progress: (Theme.windPhase * (3 + seed % 3)
                        + Theme.windTravel * (2 + seed % 2) + seed * 0.618034) % 1
            width: 12; height: 19
            scale: 0.48 + (seed % 5) * 0.13
            x: parent.width + 30 - progress * (parent.width + 90)
            y: headerOnly ? 10 + (index % 5) * 16 + progress * 28
                             + Math.sin(progress * Math.PI * 4 + seed) * 7
                          : 20 + (index % 6) * parent.height * 0.10
                             + progress * parent.height * (0.18 + (seed % 5) * 0.11)
                             + Math.sin(progress * Math.PI * 3 + seed) * 18
            rotation: seed * 37 + progress * (seed % 2 ? 360 : -280)
            opacity: Math.min(1, progress * 12, (1 - progress) * 12)
                     * (headerOnly ? 0.48 : 0.62)
            transform: Scale {
                origin.x: 6; origin.y: 9
                yScale: 0.5 + 0.5 * Math.abs(Math.sin(progress * Math.PI * 5 + seed))
            }
            ShapePath {
                strokeColor: "transparent"
                fillGradient: LinearGradient {
                    x1: 0; y1: 0; x2: 12; y2: 19
                    GradientStop { position: 0; color: "#FAD7E4" }
                    GradientStop { position: 0.6; color: "#ECA4C0" }
                    GradientStop { position: 1; color: "#D97DA4" }
                }
                startX: 6; startY: 0
                PathCubic { x: 6; y: 19; control1X: 16; control1Y: 5; control2X: 10; control2Y: 14 }
                PathCubic { x: 6; y: 0; control1X: -2; control1Y: 13; control2X: 0; control2Y: 4 }
            }
        }
    }
    // Main stream stays behind the paper; a lighter stream crosses the header.
    Item {
        id: petals
        objectName: "windPetals"
        anchors.fill: parent
        clip: true
        visible: Theme.windEnabled && !startupPreloader.running
        Repeater {
            model: root.width < 1000 ? 10 : 24
            delegate: windPetalComponent
        }
    }

    Item {
        objectName: "windHeaderPetals"
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.leftMargin: sidebar.width
        anchors.right: parent.right
        height: root.width < 1000 ? 95 : 112
        z: 1
        clip: true
        visible: Theme.windEnabled && !startupPreloader.running
        Repeater {
            model: root.width < 1000 ? 5 : 10
            delegate: windPetalComponent
        }
    }

    RowLayout {
        id: appContent
        objectName: "appContent"
        anchors.fill: parent
        spacing: 0
        opacity: startupPreloader.running ? 0 : 1
        enabled: !startupPreloader.running

        RouteSidebar {
            id: sidebar
            bridge: root.bridge
        }

        Item {
            id: pageLoader
            objectName: "pageLoader"
            Layout.fillWidth: true
            Layout.fillHeight: true
            readonly property string route: root.bridge ? root.bridge.currentPage : "home"
            readonly property var currentLoader: route === "plan" ? retainedPlan
                                       : route === "assistant" ? retainedAssistant
                                       : route === "home" ? retainedHome
                                       : route === "knowledge" ? retainedKnowledge : retainedSettings
            readonly property var item: currentLoader.item
            readonly property bool pagePresented: currentLoader.presentationReady
            RetainedLoader {
                id: retainedPlan
                dataReady: item ? item.initialDataReady : false
                property bool created: false
                active: pageLoader.route === "plan" || created
                visible: pageLoader.route === "plan"
                sourceComponent: planPageComponent
                onLoaded: { created = true; root.applyPendingRecordsTab() }
            }
            RetainedLoader {
                id: retainedAssistant
                dataReady: item ? item.initialDataReady : false
                property bool created: false
                active: pageLoader.route === "assistant" || created
                visible: pageLoader.route === "assistant"
                sourceComponent: assistantPageComponent
                onLoaded: created = true
            }
            RetainedLoader {
                id: retainedHome
                dataReady: item ? item.initialDataReady : false
                property bool created: false
                active: pageLoader.route === "home" || created
                visible: pageLoader.route === "home"
                sourceComponent: homePageComponent
                onLoaded: created = true
            }
            RetainedLoader {
                id: retainedKnowledge
                dataReady: item ? item.initialDataReady : false
                property bool created: false
                active: pageLoader.route === "knowledge" || created
                visible: pageLoader.route === "knowledge"
                sourceComponent: knowledgePageComponent
                onLoaded: created = true
            }
            RetainedLoader {
                id: retainedSettings
                dataReady: item ? item.initialDataReady : false
                property bool created: false
                active: pageLoader.route === "settings" || created
                visible: pageLoader.route === "settings"
                sourceComponent: settingsPageComponent
                onLoaded: created = true
            }

            BusyIndicator {
                anchors.centerIn: parent
                running: pageLoader.currentLoader.status !== Loader.Error && !pageLoader.currentLoader.presentationReady
                visible: running
                Accessible.name: "正在打开页面"
            }
            BodyText {
                objectName: "pageLoadError"
                anchors.centerIn: parent
                width: Math.max(0, parent.width - 48)
                horizontalAlignment: Text.AlignHCenter
                visible: pageLoader.currentLoader.status === Loader.Error
                text: "页面未能加载，请重新启动应用后重试。"
                color: Theme.warning
            }

        }
    }

    StartupPreloader {
        id: startupPreloader
        bridge: root.bridge
        pageLoader: root.contentLoader
    }

    Rectangle {
        objectName: "startupCover"
        anchors.fill: parent
        z: 100
        visible: startupPreloader.running
        color: Theme.background
        ColumnLayout {
            anchors.centerIn: parent
            width: Math.min(420, parent.width - 48)
            spacing: 18
            BodyText {
                Layout.alignment: Qt.AlignHCenter
                text: "和纸学习室"
                horizontalAlignment: Text.AlignHCenter
                font.family: Theme.headingFontFamily
                font.pixelSize: 36
            }
            BusyIndicator { Layout.alignment: Qt.AlignHCenter; running: parent.parent.visible }
            BodyText {
                objectName: "startupLabel"
                Layout.alignment: Qt.AlignHCenter
                text: "正在准备" + startupPreloader.label + "…"
                horizontalAlignment: Text.AlignHCenter
                color: Theme.muted
            }
            ProgressBar {
                id: startupProgress
                Layout.fillWidth: true
                value: startupPreloader.progress
                background: Rectangle { implicitHeight: 8; color: Theme.line; radius: 4 }
                contentItem: Item {
                    implicitHeight: 8
                    Rectangle {
                        width: parent.width * startupProgress.visualPosition
                        height: parent.height
                        radius: 4
                        color: Theme.accent
                    }
                }
            }
            BodyText {
                Layout.alignment: Qt.AlignHCenter
                text: "准备完成后即可切换页面"
                horizontalAlignment: Text.AlignHCenter
                color: Theme.muted
                font.pixelSize: Theme.captionFontSize
            }
        }
    }

    Connections {
        target: root.bridge
        function onCurrentPageChanged() {
            if (!root.bridge || root.bridge.currentPage !== "plan") {
                root.openRecordsAfterNavigation = false
                root.pendingRecordNote = ""
                recordsTabTimer.stop()
                return
            }
            recordsTabTimer.restart()
        }
    }

    Component {
        id: homePageComponent
        HomePage {
            bridge: root.bridge
            onRecordRequested: (note) => root.openRecords(note)
            onPlanRequested: {
                if (!root.bridge) return
                root.bridge.navigate("plan")
                if (retainedPlan.item) retainedPlan.item.section = 0
            }
        }
    }

    Component {
        id: planPageComponent
        PlanPage { bridge: root.bridge }
    }

    Component {
        id: assistantPageComponent
        AssistantPage { bridge: root.bridge }
    }

    Component {
        id: knowledgePageComponent
        KnowledgePage { bridge: root.bridge }
    }

    Component {
        id: settingsPageComponent
        SettingsPage { bridge: root.bridge }
    }

    Shortcut {
        sequence: "Ctrl+Q"
        context: Qt.WindowShortcut
        onActivated: if (root.bridge && root.bridge.lifecycle) root.bridge.lifecycle.requestQuit()
    }
    Shortcut {
        sequence: "Ctrl+B"
        enabled: !startupPreloader.running
        context: Qt.WindowShortcut
        onActivated: if (root.bridge) root.bridge.toggleSidebar()
    }
    Shortcut {
        sequence: "Ctrl+1"
        enabled: !startupPreloader.running
        context: Qt.WindowShortcut
        onActivated: if (root.bridge) root.bridge.navigate("home")
    }
    Shortcut {
        sequence: "Ctrl+2"
        enabled: !startupPreloader.running
        context: Qt.WindowShortcut
        onActivated: if (root.bridge) root.bridge.navigate("plan")
    }
    Shortcut {
        sequence: "Ctrl+3"
        enabled: !startupPreloader.running
        context: Qt.WindowShortcut
        onActivated: if (root.bridge) root.bridge.navigate("assistant")
    }
    Shortcut {
        sequence: "Ctrl+4"
        enabled: !startupPreloader.running
        context: Qt.WindowShortcut
        onActivated: if (root.bridge) root.bridge.navigate("knowledge")
    }
    Shortcut {
        sequence: "Ctrl+5"
        enabled: !startupPreloader.running
        context: Qt.WindowShortcut
        onActivated: if (root.bridge) root.bridge.navigate("settings")
    }
    Shortcut {
        sequence: "F5"
        enabled: !startupPreloader.running
        context: Qt.WindowShortcut
        onActivated: {
            if (root.bridge) root.bridge.refresh()
            if (root.bridge && root.bridge.currentPage === "plan" && root.bridge.workspace && !root.bridge.workspace.busy)
                root.bridge.workspace.run("load", {})
            if (root.bridge && root.bridge.insights && !root.bridge.insights.busy) {
                if (root.bridge.currentPage === "assistant") root.bridge.insights.run("assistant.load", {})
                if (root.bridge.currentPage === "knowledge") root.bridge.insights.run("knowledge.load", {})
            }
            if (root.bridge && root.bridge.currentPage === "settings" && root.bridge.settings)
                root.bridge.settings.run("load", {})
        }
    }
}
