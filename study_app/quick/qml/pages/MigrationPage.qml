import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "../components"

Item {
    id: root
    objectName: "migrationPage"
    required property var bridge
    required property string pageKey
    property var snapshot: bridge ? (bridge.snapshot || ({})) : ({})
    property var routeInfo: {
        const list = bridge ? (bridge.routes || []) : []
        for (let i = 0; i < list.length; ++i) {
            if (list[i].key === pageKey) return list[i]
        }
        return ({ key: pageKey, label: pageKey, migrated: false, description: "" })
    }

    function display(value, fallback) {
        if (value === undefined || value === null || value === "") return fallback === undefined ? "暂无" : fallback
        if (typeof value === "object") return JSON.stringify(value)
        return String(value)
    }

    function quantity(value, unit) {
        if (value === undefined || value === null || value === "") return "未知"
        return String(value) + unit
    }

    ScrollView {
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth
        contentHeight: body.implicitHeight + 48
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            id: body
            x: 24
            y: 20
            width: Math.max(0, parent.width - 48)
            spacing: 16

            Text {
                text: root.display(root.routeInfo.label, "页面")
                textFormat: Text.PlainText
                color: Theme.ink
                font.family: Theme.fontFamily
                font.pixelSize: 30
                font.weight: Font.DemiBold
            }

            PaperCard {
                Layout.fillWidth: true
                paperColor: root.routeInfo.migrated ? Theme.paper : Theme.peach
                RowLayout {
                    Layout.fillWidth: true
                    StatusPill {
                        text: root.routeInfo.migrated ? "已接入 Qt Quick" : "尚未迁移"
                        tone: root.routeInfo.migrated ? Theme.leaf : Theme.accent
                    }
                    Text {
                        Layout.fillWidth: true
                        text: root.display(root.routeInfo.description, "")
                        textFormat: Text.PlainText
                        color: Theme.ink
                        font.family: Theme.fontFamily
                        font.pixelSize: 16
                        wrapMode: Text.Wrap
                    }
                }
                Text {
                    Layout.fillWidth: true
                    text: root.routeInfo.migrated
                          ? "此页面会在后续阶段接入现有业务服务。"
                          : "此页面尚未迁移到 Qt Quick。这里展示真实数据摘要；完整业务仍由旧 Widgets 页面承载。"
                    textFormat: Text.PlainText
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionFontSize
                    wrapMode: Text.Wrap
                }
            }

            GridLayout {
                Layout.fillWidth: true
                columns: width >= 800 ? 3 : 1
                columnSpacing: 12
                rowSpacing: 12
                Repeater {
                    model: [
                        { label: "活动学科", value: root.snapshot.activeCount },
                        { label: "已归档学科", value: root.snapshot.archivedCount },
                        { label: "学习记录", value: root.snapshot.recordCount }
                    ]
                    delegate: PaperCard {
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.minimumWidth: 180
                        padding: 14
                        Text {
                            text: modelData.label
                            textFormat: Text.PlainText
                            color: Theme.muted
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.captionFontSize
                        }
                        Text {
                            text: root.display(modelData.value, "—")
                            textFormat: Text.PlainText
                            color: Theme.ink
                            font.family: Theme.fontFamily
                            font.pixelSize: 24
                            font.weight: Font.DemiBold
                        }
                    }
                }
            }

            PaperCard {
                Layout.fillWidth: true
                Text {
                    text: "当前学科"
                    textFormat: Text.PlainText
                    color: Theme.ink
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.bodyFontSize
                    font.weight: Font.DemiBold
                }
                Repeater {
                    model: root.snapshot.subjects || []
                    delegate: ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Text {
                            text: root.display(modelData.name, "未命名学科")
                            textFormat: Text.PlainText
                            color: Theme.ink
                            font.family: Theme.fontFamily
                            font.pixelSize: 16
                            font.weight: Font.DemiBold
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                        Text {
                            text: "近期周期评分 " + (modelData.hasWindowRecords ? root.quantity(modelData.windowScore, " 分") : "暂无记录")
                                  + " · 全课程模型估计 " + root.quantity(modelData.mastery, "%")
                                  + " · 已覆盖掌握估计 " + root.quantity(modelData.coveredMastery, "%")
                                  + " · 覆盖 " + root.display(modelData.covered, "0")
                                  + " / " + root.display(modelData.total, "0")
                            textFormat: Text.PlainText
                            color: Theme.muted
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.captionFontSize
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }
                }
                Text {
                    visible: (root.snapshot.subjects || []).length === 0
                    Layout.fillWidth: true
                    text: "当前没有活动学科。"
                    textFormat: Text.PlainText
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionFontSize
                }
            }

            PaperCard {
                Layout.fillWidth: true
                Text {
                    text: "最近学习记录"
                    textFormat: Text.PlainText
                    color: Theme.ink
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.bodyFontSize
                    font.weight: Font.DemiBold
                }
                Repeater {
                    model: root.snapshot.recentRecords || []
                    delegate: Text {
                        required property var modelData
                        Layout.fillWidth: true
                        text: root.display(modelData.date, "") + "  "
                              + root.display(modelData.subject, "") + "  "
                              + root.display(modelData.detail, "")
                        textFormat: Text.PlainText
                        color: Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.captionFontSize
                        wrapMode: Text.Wrap
                    }
                }
                Text {
                    visible: (root.snapshot.recentRecords || []).length === 0
                    Layout.fillWidth: true
                    text: "当前没有近期记录。"
                    textFormat: Text.PlainText
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionFontSize
                }
            }

            ActionButton {
                text: "返回今日概览"
                primary: true
                accessibleLabel: "返回今日概览首页"
                Layout.alignment: Qt.AlignLeft
                onClicked: if (root.bridge) root.bridge.navigate("home")
            }
        }
    }
}
