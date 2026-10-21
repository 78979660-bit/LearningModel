import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "../components"

Item {
    id: root
    objectName: "settingsPage"
    required property var bridge
    readonly property var work: bridge ? bridge.settings : null
    readonly property bool initialDataReady: !work || Object.keys(work.settingsData).length > 0 || !!work.error
    readonly property bool preloadReady: !work || !!work.error ||
        (initialDataReady && (activeTab === "subjects" ? subjectsPane.initialDataReady : preferencesPane.snapshotReady))
    property string activeTab: "preferences"
    property bool pageReady: false
    property bool entered: false
    function enterPage() {
        if (!visible || entered || !work) return
        entered = true
        work.loadForPage()
    }
    Component.onCompleted: { pageReady = true; enterPage() }
    onVisibleChanged: {
        if (!visible) entered = false
        else if (pageReady) enterPage()
    }
    onActiveTabChanged: if (work) work.invalidatePreview("subject")

    ColumnLayout {
        x: root.width < 820 ? 14 : Theme.pageMargin
        y: root.height < 700 ? 16 : Theme.pageMargin
        width: Math.min(1000, root.width - x * 2)
        height: root.height - y * 2
        spacing: 8
        RowLayout {
            Layout.fillWidth: true
            Layout.minimumHeight: root.height < 700 ? 72 : 126
            spacing: 10
            ColumnLayout {
                Layout.fillWidth: false
                spacing: 0
                BodyText {
                    text: "设置"
                    font.family: Theme.headingFontFamily
                    font.pixelSize: root.width >= 900 ? Theme.pageTitleSize : 36
                    font.weight: Font.DemiBold
                }
            }
            BusyIndicator { running: root.work && root.work.busy; opacity: running ? 1 : 0; implicitWidth: 28; implicitHeight: 28 }
            ActionButton { objectName: "settingsRefresh"; text: ""; iconSource: Qt.resolvedUrl("../assets/icons/refresh.svg"); accessibleLabel: "刷新设置"; enabled: root.work && !root.work.busy; onClicked: root.work.run("load", {}) }
            Item { Layout.fillWidth: true }
        }
        RowLayout {
            Layout.alignment: Qt.AlignLeft
            RowLayout {
                Layout.fillWidth: true
                spacing: 5
                ActionButton { objectName: "settingsPreferencesTab"; text: "偏好设置"; primary: root.activeTab === "preferences"; Accessible.role: Accessible.PageTab; Accessible.selected: root.activeTab === "preferences"; onClicked: root.activeTab = "preferences" }
                ActionButton { objectName: "settingsSubjectsTab"; text: "学科管理"; primary: root.activeTab === "subjects"; Accessible.role: Accessible.PageTab; Accessible.selected: root.activeTab === "subjects"; onClicked: root.activeTab = "subjects" }
                Item { Layout.fillWidth: true }
            }
        }
        PaperCard {
            Layout.fillWidth: true
            visible: root.work && (!!root.work.error ||
                     (root.work.message && root.work.message !== "已读取真实设置与学科目录。" &&
                      root.work.message !== "已刷新真实数据。"))
            paperColor: root.work && root.work.error ? Theme.pinkSoft : Theme.card
            BodyText { objectName: "settingsMessage"; text: root.work ? root.work.error || root.work.message : ""; color: root.work && root.work.error ? Theme.warning : Theme.muted }
            ActionButton { text: "停止接收结果"; visible: root.work && root.work.canCancel; onClicked: root.work.cancel() }
        }
        PreferencesPane { id: preferencesPane; Layout.fillWidth: true; Layout.fillHeight: true; visible: root.activeTab === "preferences"; work: root.work }
        SubjectsPane { id: subjectsPane; Layout.fillWidth: true; Layout.fillHeight: true; visible: root.activeTab === "subjects"; work: root.work }
    }
}
