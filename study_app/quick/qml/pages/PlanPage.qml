import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "../components"

Item {
    id: root
    objectName: "planPage"
    required property var bridge
    readonly property var workspace: bridge ? bridge.workspace : null
    readonly property bool initialDataReady: !workspace || Object.keys(workspace.planData).length > 0 || !!workspace.error
    property int section: 0
    readonly property bool panesPresented: paneLoader.panePresented
    readonly property bool preloadReady: initialDataReady && paneLoader.paneDataReady && panesPresented
    property string recordNotePrefill: ""
    function applyRecordNote(value) {
        const next = String(value || "")
        if (recordNotePrefill === next && recordsLoader.item)
            recordsLoader.item.applyPrefill(next)
        else recordNotePrefill = next
    }

    property bool pageReady: false
    property bool entered: false
    function enterPage() {
        if (!visible || entered || !workspace) return
        entered = true
        workspace.loadForPage()
    }
    Component.onCompleted: { pageReady = true; enterPage() }
    onVisibleChanged: {
        if (!visible) entered = false
        else if (pageReady) enterPage()
    }

    ColumnLayout {
        width: Math.min(1320, root.width - (root.width < 820 ? 36 : Theme.pageMargin * 2))
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.topMargin: root.width < 820 ? 18 : Theme.pageMargin
        anchors.bottomMargin: root.width < 820 ? 18 : Theme.pageMargin
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            BodyText {
                Layout.fillWidth: false
                text: "学习计划"
                font.family: Theme.headingFontFamily
                font.pixelSize: root.width >= 900 ? Theme.pageTitleSize : 36
                font.weight: Font.DemiBold
            }
            ActionButton {
                objectName: "workspaceRefresh"
                text: ""
                iconSource: Qt.resolvedUrl("../assets/icons/refresh.svg")
                accessibleLabel: "刷新学习计划与记录"
                enabled: root.workspace && !root.workspace.busy
                onClicked: root.workspace.run("load", {})
            }
            Item { Layout.fillWidth: true }
        }

        PaperCard {
            Layout.fillWidth: true
            visible: !root.workspace || (root.workspace.busy && root.workspace.operation !== "刷新学习工作区") || !!root.workspace.error
                     || (String(root.workspace.message || "").trim().length > 0
                         && String(root.workspace.message || "").trim() !== "已刷新真实数据。")
            padding: 9
            paperColor: Theme.lavender
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                BusyIndicator {
                    running: root.workspace ? root.workspace.busy : false
                    visible: running
                    implicitWidth: 24
                    implicitHeight: 24
                }
                BodyText {
                    objectName: "workspaceStatus"
                    Layout.fillWidth: true
                    text: !root.workspace ? "学习服务尚未连接。"
                          : root.workspace.busy ? root.workspace.operation
                          : (root.workspace.error || root.workspace.message || "预览不会写入学习数据。请复核后确认保存。")
                    color: root.workspace && root.workspace.error ? Theme.warning : Theme.ink
                    Accessible.role: Accessible.StaticText
                }
                ActionButton {
                    objectName: "workspaceCancel"
                    text: "停止"
                    visible: root.workspace ? root.workspace.busy : false
                    enabled: root.workspace ? root.workspace.canCancel : false
                    onClicked: root.workspace.cancel()
                }
            }
        }

        TabBar {
            id: tabs
            objectName: "workspaceTabs"
            implicitWidth: 480
            Layout.preferredWidth: 480
            Layout.maximumWidth: parent.width
            Layout.alignment: Qt.AlignLeft
            currentIndex: root.section
            onCurrentIndexChanged: root.section = currentIndex
            Repeater {
                model: [
                    {key: "plan", label: "计划"},
                    {key: "records", label: "学习记录"},
                    {key: "oj", label: "OJ"},
                    {key: "practice", label: "练习 PDF"}
                ]
                TabButton {
                    required property var modelData
                    objectName: "tab-" + modelData.key
                    text: modelData.label
                    Accessible.name: modelData.key === "oj" ? "OJ 题目与提交" : text
                    width: tabs.width / 4
                    implicitHeight: 42
                    contentItem: Text {
                        text: parent.text
                        textFormat: Text.PlainText
                        color: Theme.ink
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.bodyFontSize
                        font.weight: Font.DemiBold
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                    }
                    background: Rectangle {
                        color: parent.checked ? Theme.accentSoft : Theme.paper
                        border.color: parent.activeFocus ? Theme.accent : Theme.line
                        border.width: parent.activeFocus ? 2 : 1
                        radius: Theme.controlRadius
                    }
                }
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Item {
                id: paneLoader
                objectName: "workspacePaneLoader"
                anchors.fill: parent
                readonly property var currentLoader: [planningLoader, recordsLoader, ojLoader, practiceLoader][root.section]
                readonly property bool paneReady: currentLoader.status === Loader.Ready
                readonly property bool panePresented: currentLoader.presentationReady
                readonly property bool paneDataReady: currentLoader.item ? currentLoader.item.initialDataReady : false
                function loadPane() {
                    // Create a tab on its first visit and preserve its form on
                    // return. Reviews still expire when leaving a tab.
                    if (currentLoader.source.toString().length > 0) return
                    const names = ["PlanningPane.qml", "RecordsPane.qml", "OJPane.qml", "PracticePane.qml"]
                    const props = {workspace: root.workspace}
                    if (root.section === 0) props.surfacesReady = Qt.binding(function() { return planningLoader.status === Loader.Ready })
                    if (root.section === 1) props.prefillNote = Qt.binding(function() { return root.recordNotePrefill })
                    currentLoader.setSource(Qt.resolvedUrl(names[root.section]), props)
                }
                Component.onCompleted: loadPane()
                Connections {
                    target: root
                    function onSectionChanged() {
                        if (root.workspace) {
                            for (const kind of ["budget", "plan", "record", "practice"])
                                root.workspace.invalidatePreview(kind)
                        }
                        paneLoader.loadPane()
                    }
                }
                RetainedLoader {
                    id: planningLoader
                    objectName: "planningTabLoader"
                    dataReady: item ? item.initialDataReady : false
                    asynchronous: true
                    visible: root.section === 0
                }
                RetainedLoader {
                    id: recordsLoader
                    objectName: "recordsTabLoader"
                    dataReady: item ? item.initialDataReady : false
                    asynchronous: true
                    visible: root.section === 1
                }
                RetainedLoader {
                    id: ojLoader
                    objectName: "ojTabLoader"
                    dataReady: item ? item.initialDataReady : false
                    asynchronous: true
                    visible: root.section === 2
                }
                RetainedLoader {
                    id: practiceLoader
                    objectName: "practiceTabLoader"
                    dataReady: item ? item.initialDataReady : false
                    asynchronous: true
                    visible: root.section === 3
                }
                BusyIndicator {
                    anchors.centerIn: parent
                    running: !paneLoader.panePresented
                    visible: running
                    Accessible.name: "正在打开工作区"
                }
            }
        }
    }
}
