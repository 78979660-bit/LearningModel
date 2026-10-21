import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "../components"

Item {
    id: root
    objectName: "homeSummaryPane"
    required property var bridge
    readonly property var snapshot: summarySnapshot.value
    SnapshotView {
        id: summarySnapshot
        objectName: "summarySnapshot"
        source: root.bridge
        dataProperty: "summaryData"
        pageVisible: root.visible
        // A rejected switch write deliberately re-notifies the same DTO to
        // restore the control to its persisted value.
        refreshOnNotify: true
    }
    readonly property var summary: snapshot.summary || ({})
    readonly property bool busy: bridge ? !!bridge.busy : false
    readonly property string errorText: bridge ? String(bridge.error || "") : ""
    readonly property string messageText: bridge ? String(bridge.message || "") : ""
    implicitWidth: content.implicitWidth
    implicitHeight: content.implicitHeight

    function list(value) {
        if (value === undefined || value === null) return []
        if (typeof value === "string") return value.length > 0 ? [value] : []
        if (Array.isArray(value)) return value
        if (typeof value.length === "number") return value
        return []
    }

    function sections() {
        return [
            { key: "overview", title: "概况" },
            { key: "risks", title: "风险" },
            { key: "actions", title: "建议" }
        ]
    }

    Component.onCompleted: if (bridge && visible) bridge.enter_page()
    onVisibleChanged: {
        if (!bridge) return
        if (visible) bridge.enter_page()
        else bridge.leave_page()
    }

    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: 12

        PaperCard {
            Layout.fillWidth: true
            padding: 18

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3
                    Text {
                        text: "每日学习总结"
                        textFormat: Text.PlainText
                        color: Theme.ink
                        font.family: Theme.headingFontFamily
                        font.pixelSize: 24
                        font.weight: Font.DemiBold
                    }
                    Text {
                        text: String(root.snapshot.date || "")
                              + (root.snapshot.mode === "enhanced" ? " · 云端增强" : " · 本地规则")
                        textFormat: Text.PlainText
                        color: Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.captionFontSize
                    }
                }
                ActionButton {
                    objectName: "dailySummaryRefresh"
                    text: ""
                    iconSource: Qt.resolvedUrl("../assets/icons/refresh.svg")
                    accessibleLabel: "刷新每日学习总结"
                    enabled: root.bridge && !root.busy
                    onClicked: root.bridge.run("refresh", ({}))
                }
            }

            Text {
                Layout.fillWidth: true
                visible: !!root.snapshot.stale
                text: "数据已变化，请刷新"
                textFormat: Text.PlainText
                color: root.snapshot.stale ? Theme.warning : Theme.muted
                font.family: Theme.fontFamily
                font.pixelSize: Theme.captionFontSize
                wrapMode: Text.Wrap
            }

            Repeater {
                model: root.sections()
                delegate: ColumnLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: 4

                    Text {
                        Layout.fillWidth: true
                        text: modelData.title
                        textFormat: Text.PlainText
                        color: Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                    }
                    Repeater {
                        model: root.list(root.summary[modelData.key])
                        delegate: Text {
                            required property var modelData
                            Layout.fillWidth: true
                            text: "• " + String(modelData)
                            textFormat: Text.PlainText
                            color: Theme.ink
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.bodyFontSize
                            wrapMode: Text.Wrap
                        }
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                visible: root.list(root.summary.source).length > 0
                text: root.list(root.summary.source).join("\n")
                textFormat: Text.PlainText
                color: Theme.muted
                font.family: Theme.fontFamily
                font.pixelSize: Theme.captionFontSize
                wrapMode: Text.Wrap
            }
        }

        PaperCard {
            Layout.fillWidth: true
            padding: 14
            paperColor: root.errorText ? Theme.pinkSoft : Theme.paper

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Text {
                    Layout.fillWidth: true
                    text: String(root.snapshot.enhancedStatus || "云端每日总结尚未检查。")
                    textFormat: Text.PlainText
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionFontSize
                    wrapMode: Text.Wrap
                }
                Switch {
                    id: llmToggle
                    objectName: "dailySummaryLlmMode"
                    Accessible.name: "主页 LLM 增强模式"
                    text: "LLM 增强模式"
                    enabled: root.bridge && !root.busy
                    onClicked: root.bridge.run("setLlmMode", { "enabled": !root.snapshot.llmModeEnabled })
                    Binding {
                        target: llmToggle
                        property: "checked"
                        value: !!root.snapshot.llmModeEnabled
                        when: true
                        restoreMode: Binding.RestoreBindingOrValue
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                ActionButton {
                    objectName: "dailySummaryGenerateEnhanced"
                    text: root.busy && root.bridge && root.bridge.canCancel ? "正在生成…" : "生成云端总结"
                    primary: true
                    accessibleLabel: "显式生成云端每日学习总结"
                    enabled: root.bridge && !root.busy && !!root.snapshot.canGenerateEnhanced
                    onClicked: root.bridge.run("generateEnhanced", ({}))
                }
                ActionButton {
                    objectName: "dailySummaryStop"
                    visible: root.bridge && root.bridge.canCancel
                    text: "停止接收"
                    accessibleLabel: "停止接收每日总结结果"
                    onClicked: root.bridge.cancel()
                }
                Text {
                    Layout.fillWidth: true
                    visible: root.busy
                    text: "正在生成…"
                    textFormat: Text.PlainText
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionFontSize
                    wrapMode: Text.Wrap
                }
            }

            Text {
                objectName: "dailySummaryError"
                Layout.fillWidth: true
                visible: root.errorText.length > 0
                text: root.errorText
                textFormat: Text.PlainText
                color: Theme.warning
                font.family: Theme.fontFamily
                font.pixelSize: 16
                wrapMode: Text.Wrap
            }
            Text {
                Layout.fillWidth: true
                visible: !root.errorText && root.messageText.length > 0
                text: root.messageText
                textFormat: Text.PlainText
                color: Theme.muted
                font.family: Theme.fontFamily
                font.pixelSize: Theme.captionFontSize
                wrapMode: Text.Wrap
            }
        }
    }
}
