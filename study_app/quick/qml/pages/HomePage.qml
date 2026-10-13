import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "../components"

Item {
    id: root
    signal planRequested()
    objectName: "homePage"
    required property var bridge
    signal recordRequested(string note)
    readonly property var snapshot: homeSnapshot.value
    SnapshotView {
        id: homeSnapshot
        objectName: "homeSnapshot"
        source: root.bridge
        dataProperty: "snapshot"
        pageVisible: root.visible
    }
    property bool hasData: bridge ? bridge.hasData : false
    property bool busy: bridge ? bridge.busy : true
    property string errorMessage: bridge ? String(bridge.error || "") : ""
    readonly property bool initialDataReady: hasData || errorMessage.length > 0
    readonly property bool preloadReady: homeSnapshot.ready || errorMessage.length > 0
    property var todayPlan: snapshot.todayPlan || ({
        status: "loading", title: "正在读取今日计划", detail: "", items: []
    })
    property bool detailsExpanded: false
    property string quickNoteDraft: ""
    readonly property int contentMaxWidth: 1320

    function display(value, fallback) {
        if (value === undefined || value === null || value === "")
            return fallback === undefined ? "暂无" : fallback
        if (typeof value === "object") return JSON.stringify(value)
        return String(value)
    }
    function list(value) {
        if (value === undefined || value === null) return []
        if (typeof value === "string") return value.length ? [value] : []
        if (typeof value.length === "number") return value
        return []
    }
    function planStatus(value) {
        const labels = {
            confirmed: "已确认", legacy: "旧版计划", empty: "暂无计划",
            unavailable: "暂不可用", error: "读取异常", loading: "正在加载"
        }
        return labels[String(value)] || display(value, "")
    }
    function timeOnly(value) {
        const text = String(value || "")
        const marker = text.indexOf("T")
        return marker >= 0 && text.length >= marker + 9 ? text.slice(marker + 1, marker + 9) : text
    }
    function quantity(value, unit) {
        if (value === undefined || value === null || value === "") return "未知"
        return String(value) + unit
    }
    function hasNumber(value) {
        return value !== undefined && value !== null && value !== "" && isFinite(Number(value))
    }
    function budgetLabel() {
        if (todayPlan.status === "confirmed" && hasNumber(todayPlan.budgetMinutes))
            return String(todayPlan.budgetMinutes)
        if (todayPlan.status === "loading") return "读取中"
        if (todayPlan.status === "empty" || todayPlan.status === "legacy") return "待设定"
        return "未知"
    }
    function budgetDetail() {
        if (todayPlan.status === "confirmed") {
            if (hasNumber(todayPlan.plannedMinutes) && hasNumber(todayPlan.remainingMinutes))
                return "已安排 " + todayPlan.plannedMinutes + " 分钟 · 余量 " + todayPlan.remainingMinutes + " 分钟"
            return "今日预算已确认"
        }
        return ""
    }
    function planSubject(item) {
        const title = String(item.title || "")
        const splitAt = title.indexOf(" / ")
        return splitAt >= 0 ? title.slice(0, splitAt) : ""
    }
    function planTopic(item) {
        const title = String(item.title || "")
        const splitAt = title.indexOf(" / ")
        return splitAt >= 0 ? title.slice(splitAt + 3) : title
    }
    function planDisplayTitle() {
        if (todayPlan.status === "confirmed" && String(todayPlan.title || "") === "今日已确认计划")
            return ""
        return display(todayPlan.title, "暂无已确认计划")
    }

    ScrollView {
        id: scroll
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth
        contentHeight: body.implicitHeight + Theme.pageMargin * 2
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            id: body
            x: Math.max(Theme.pageMargin, (scroll.availableWidth - width) / 2)
            y: Theme.pageMargin
            width: Math.max(0, Math.min(root.contentMaxWidth, scroll.availableWidth - Theme.pageMargin * 2))
            spacing: Theme.panelSpacing

            RowLayout {
                Layout.fillWidth: true
                Layout.minimumHeight: root.width >= 1100 ? 156 : 112
                spacing: 14
                ColumnLayout {
                    Layout.preferredWidth: root.width >= 1100 ? 340 : 250
                    Layout.minimumWidth: 180
                    spacing: 4
                    Text {
                        text: "今日概览"
                        textFormat: Text.PlainText
                        color: Theme.ink
                        font.family: Theme.headingFontFamily
                        font.pixelSize: root.width >= 1100 ? 64 : 44
                        font.weight: Font.DemiBold
                    }
                    Text {
                        text: root.display(root.snapshot.date, "")
                        textFormat: Text.PlainText
                        color: Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.bodyFontSize
                    }
                }
                ActionButton {
                    objectName: "homeRefreshButton"
                    text: ""
                    iconSource: Qt.resolvedUrl("../assets/icons/refresh.svg")
                    accessibleLabel: "刷新首页真实数据"
                    implicitWidth: 44
                    implicitHeight: 44
                    Layout.preferredWidth: 44
                    Layout.preferredHeight: 44
                    padding: 8
                    enabled: !root.busy
                    ToolTip.visible: hovered
                    ToolTip.text: accessibleLabel
                    onClicked: if (root.bridge) root.bridge.refresh()
                }
                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: 58
                    color: Theme.line
                }
                ColumnLayout {
                    Layout.preferredWidth: 235
                    Layout.minimumWidth: 195
                    spacing: 1
                    Text {
                        text: "今日可用预算"
                        textFormat: Text.PlainText
                        color: Theme.ink
                        font.family: Theme.fontFamily
                        font.pixelSize: 16
                    }
                    RowLayout {
                        spacing: 6
                        Text {
                            objectName: "homeBudgetValue"
                            text: root.budgetLabel()
                            textFormat: Text.PlainText
                            color: Theme.accent
                            font.family: Theme.headingFontFamily
                            font.pixelSize: 44
                            font.weight: Font.DemiBold
                        }
                        Text {
                            visible: root.todayPlan.status === "confirmed" && root.hasNumber(root.todayPlan.budgetMinutes)
                            text: "分钟"
                            textFormat: Text.PlainText
                            color: Theme.muted
                            font.family: Theme.fontFamily
                            font.pixelSize: 16
                            Layout.alignment: Qt.AlignBottom
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: root.budgetDetail().length > 0
                        text: root.budgetDetail()
                        textFormat: Text.PlainText
                        color: Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.captionFontSize
                        wrapMode: Text.Wrap
                    }
                }
                Item { Layout.fillWidth: true }
            }

            PaperCard {
                objectName: "homeLoadError"
                visible: root.errorMessage.length > 0
                Layout.fillWidth: true
                padding: 10
                paperColor: Theme.pinkSoft
                Text {
                    Layout.fillWidth: true
                    text: "刷新失败，保留最近一次已加载的数据：" + root.errorMessage
                    textFormat: Text.PlainText
                    color: Theme.warning
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionFontSize
                    wrapMode: Text.Wrap
                }
            }
            PaperCard {
                objectName: "homeEmptyState"
                visible: !root.hasData
                Layout.fillWidth: true
                Layout.minimumHeight: 145
                paperColor: root.errorMessage ? Theme.pinkSoft : Theme.paper
                Text {
                    text: root.busy ? "正在读取本机学习数据…"
                          : (root.errorMessage ? "首页数据读取失败" : "当前没有可显示的首页数据")
                    textFormat: Text.PlainText
                    color: root.errorMessage ? Theme.warning : Theme.ink
                    font.family: Theme.headingFontFamily
                    font.pixelSize: Theme.sectionFontSize
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    visible: false
                    text: root.busy ? ""
                                    : "可以刷新后重新读取学习数据。"
                    textFormat: Text.PlainText
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: 16
                    wrapMode: Text.Wrap
                }
                ActionButton {
                    visible: !root.busy
                    text: "重新加载"
                    primary: true
                    accessibleLabel: "重新加载首页数据"
                    Layout.alignment: Qt.AlignLeft
                    onClicked: if (root.bridge) root.bridge.refresh()
                }
            }

            GridLayout {
                id: overview
                objectName: "homeOverview"
                visible: root.hasData
                Layout.fillWidth: true
                columns: width >= 900 ? 2 : 1
                columnSpacing: 16
                rowSpacing: 16

                PaperCard {
                    objectName: "homeTodayPlanCard"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredWidth: overview.width * 0.61
                    Layout.minimumWidth: overview.columns > 1 ? 320 : 0
                    Layout.rowSpan: overview.columns > 1 ? 2 : 1
                    padding: overview.columns > 1 ? 28 : 20
                    paperColor: Theme.paper
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: "今日学习安排"
                            textFormat: Text.PlainText
                            color: Theme.ink
                            font.family: Theme.headingFontFamily
                            font.pixelSize: 28
                            font.weight: Font.DemiBold
                        }
                        StatusPill {
                            text: root.planStatus(root.todayPlan.status)
                            tone: root.todayPlan.status === "confirmed" ? Theme.leaf : Theme.accent
                        }
                    }
                    Text {
                        visible: root.planDisplayTitle().length > 0
                        Layout.fillWidth: true
                        text: root.planDisplayTitle()
                        textFormat: Text.PlainText
                        color: Theme.ink
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.bodyFontSize
                        font.weight: Font.DemiBold
                        wrapMode: Text.Wrap
                    }
                    Text {
                        visible: root.todayPlan.status === "error"
                        Layout.fillWidth: true
                        text: root.display(root.todayPlan.detail, "")
                        textFormat: Text.PlainText
                        color: Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.captionFontSize
                        wrapMode: Text.Wrap
                    }
                    Repeater {
                        model: root.list(root.todayPlan.items)
                        delegate: RowLayout {
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.minimumHeight: 102
                            Layout.preferredHeight: 110
                            spacing: 10
                            Text {
                                visible: modelData.time !== undefined && modelData.time !== null && String(modelData.time).length > 0
                                text: root.display(modelData.time, "")
                                textFormat: Text.PlainText
                                color: Theme.ink
                                font.family: Theme.headingFontFamily
                                font.pixelSize: 30
                                Layout.preferredWidth: 86
                            }
                            Rectangle {
                                Layout.preferredWidth: 5
                                Layout.preferredHeight: 48
                                radius: 2
                                color: modelData.checked ? Theme.leaf : Theme.accent
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                Text {
                                    visible: root.planSubject(modelData).length > 0
                                    Layout.fillWidth: true
                                    text: root.planSubject(modelData)
                                    textFormat: Text.PlainText
                                    color: Theme.ink
                                    font.family: Theme.headingFontFamily
                                    font.pixelSize: 24
                                    font.weight: Font.DemiBold
                                    wrapMode: Text.Wrap
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: root.planTopic(modelData) || ("学习任务 " + (index + 1))
                                    textFormat: Text.PlainText
                                    color: Theme.ink
                                    font.family: Theme.headingFontFamily
                                    font.pixelSize: Theme.bodyFontSize
                                    font.weight: Font.DemiBold
                                    wrapMode: Text.Wrap
                                }
                                Text {
                                    visible: root.display(modelData.subtitle, "") !== ""
                                    Layout.fillWidth: true
                                    text: root.display(modelData.subtitle, "")
                                    textFormat: Text.PlainText
                                    color: Theme.muted
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.captionFontSize
                                    wrapMode: Text.Wrap
                                }
                            }
                            Text {
                                visible: modelData.minutes !== undefined && modelData.minutes !== null
                                text: String(modelData.minutes) + " 分钟"
                                textFormat: Text.PlainText
                                color: Theme.ink
                                font.family: Theme.fontFamily
                                font.pixelSize: 20
                            }
                        }
                    }
                    Text {
                        visible: false
                        Layout.fillWidth: true
                        text: ""
                        textFormat: Text.PlainText
                        color: Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: 16
                    }
                    Item { Layout.fillHeight: true; Layout.minimumHeight: 12 }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        ActionButton {
                            Layout.minimumWidth: overview.columns > 1 ? 220 : 160
                            Layout.preferredHeight: 52
                            objectName: "homePlanButton"
                            text: "打开学习计划"
                            primary: true
                            accessibleLabel: "打开学习计划工作区"
                            onClicked: root.planRequested()
                        }
                    }
                }

                PaperCard {
                    objectName: "homeProgressCard"
                    Layout.minimumHeight: overview.columns > 1 ? 286 : 0
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredWidth: overview.width * 0.39
                    Layout.minimumWidth: overview.columns > 1 ? 250 : 0
                    Layout.alignment: Qt.AlignTop
                    padding: overview.columns > 1 ? 26 : 20
                    paperColor: Theme.peach
                    Text {
                        text: "学科进度"
                        textFormat: Text.PlainText
                        color: Theme.ink
                        font.family: Theme.headingFontFamily
                        font.pixelSize: Theme.sectionFontSize
                        font.weight: Font.DemiBold
                    }
                    Repeater {
                        model: root.list(root.snapshot.subjects)
                        delegate: ColumnLayout {
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true
                            spacing: 5
                            RowLayout {
                                Layout.fillWidth: true
                                Text {
                                    Layout.fillWidth: true
                                    text: root.display(modelData.name, "未命名学科")
                                    textFormat: Text.PlainText
                                    color: Theme.ink
                                    font.family: Theme.fontFamily
                                    font.pixelSize: 20
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                }
                                Text {
                                    text: root.hasNumber(modelData.mastery) ? String(modelData.mastery) + "%" : "—"
                                    textFormat: Text.PlainText
                                    color: Theme.accent
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.captionFontSize
                                    font.weight: Font.DemiBold
                                }
                            }
                            Rectangle {
                                id: masteryTrack
                                Layout.fillWidth: true
                                Layout.preferredHeight: 8
                                radius: 4
                                color: Qt.alpha(Theme.ink, 0.09)
                                Rectangle {
                                    width: root.hasNumber(modelData.mastery)
                                           ? masteryTrack.width * Math.max(0, Math.min(100, Number(modelData.mastery))) / 100 : 0
                                    height: parent.height
                                    radius: parent.radius
                                    color: Theme.leaf
                                }
                            }
                            Text {
                                visible: root.detailsExpanded
                                Layout.fillWidth: true
                                text: "近期周期评分 " + (modelData.hasWindowRecords ? root.quantity(modelData.windowScore, " 分") : "暂无记录")
                                      + " · 已覆盖掌握估计 " + root.quantity(modelData.coveredMastery, "%")
                                      + " · 覆盖 " + root.display(modelData.covered, "0") + " / " + root.display(modelData.total, "0")
                                textFormat: Text.PlainText
                                color: Theme.muted
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.captionFontSize
                                wrapMode: Text.Wrap
                            }
                            Text {
                                visible: root.detailsExpanded && (root.display(modelData.latestReview, "") !== "" || root.display(modelData.latestRecord, "") !== "")
                                Layout.fillWidth: true
                                text: "最近复习：" + root.display(modelData.latestReview, root.display(modelData.latestRecord, ""))
                                textFormat: Text.PlainText
                                color: Theme.muted
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.captionFontSize
                                wrapMode: Text.Wrap
                            }
                            Repeater {
                                model: root.detailsExpanded ? root.list(modelData.warnings) : []
                                delegate: Text {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    text: "提示：" + root.display(modelData, "")
                                    textFormat: Text.PlainText
                                    color: Theme.warning
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.captionFontSize
                                    wrapMode: Text.Wrap
                                }
                            }
                            Rectangle {
                                visible: index < root.list(root.snapshot.subjects).length - 1
                                Layout.fillWidth: true
                                Layout.preferredHeight: 1
                                Layout.topMargin: 4
                                color: Theme.line
                            }
                        }
                    }
                    Text {
                        visible: root.list(root.snapshot.subjects).length === 0
                        Layout.fillWidth: true
                        text: "当前没有活动学科数据。"
                        textFormat: Text.PlainText
                        color: Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.captionFontSize
                    }
                }

                PaperCard {
                    objectName: "homeQuickNoteCard"
                    Layout.minimumHeight: overview.columns > 1 ? 260 : 0
                    curled: true
                    Layout.fillWidth: true
                    Layout.preferredWidth: overview.width * 0.39
                    Layout.minimumWidth: overview.columns > 1 ? 250 : 0
                    Layout.alignment: Qt.AlignTop
                    padding: overview.columns > 1 ? 26 : 20
                    paperColor: Theme.pinkSoft
                    Text {
                        text: "快速记录"
                        textFormat: Text.PlainText
                        color: Theme.ink
                        font.family: Theme.headingFontFamily
                        font.pixelSize: Theme.sectionFontSize
                        font.weight: Font.DemiBold
                    }
                    FormArea {
                        id: quickNote
                        objectName: "homeQuickNote"
                        accessibleLabel: "快速便笺内容"
                        placeholderText: "记下刚刚学会的内容或疑问…"
                        implicitHeight: 120
                        enabled: !!root.bridge
                        onTextChanged: root.quickNoteDraft = text
                    }
                    ActionButton {
                        objectName: "homeRecordButton"
                        text: "记录学习"
                        primary: true
                        accessibleLabel: "把快速便笺带入学习记录表单"
                        enabled: !!root.bridge
                        Layout.alignment: Qt.AlignRight
                        onClicked: root.recordRequested(quickNote.text.trim())
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                visible: root.hasData || !!root.bridge
                ActionButton {
                    objectName: "homeMoreDetailsToggle"
                    visible: root.hasData
                    text: root.detailsExpanded ? "收起详细数据" : "学习概况"
                    accessibleLabel: text
                    onClicked: root.detailsExpanded = !root.detailsExpanded
                }
                Text {
                    visible: root.hasData && root.detailsExpanded
                    Layout.fillWidth: true
                    text: root.detailsExpanded ? "数据来源、指标、建议与近期记录" : ""
                    textFormat: Text.PlainText
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionFontSize
                }
                ActionButton {
                    objectName: "homeAssistantButton"
                    text: "学习助理"
                    accessibleLabel: "打开学习助理页面"
                    onClicked: if (root.bridge) root.bridge.navigate("assistant")
                }
            }

            ColumnLayout {
                objectName: "homeDetails"
                visible: root.hasData && root.detailsExpanded
                Layout.fillWidth: true
                spacing: Theme.panelSpacing
                PaperCard {
                    objectName: "homeDataDetails"
                    Layout.fillWidth: true
                    padding: 14
                    paperColor: Theme.paper
                    Text {
                        text: "数据来源与诊断"
                        textFormat: Text.PlainText
                        color: Theme.ink
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.bodyFontSize
                        font.weight: Font.DemiBold
                    }
                    Text {
                        Layout.fillWidth: true
                        text: root.display(root.snapshot.rangeLabel, "")
                              + " · 来源 " + root.display(root.snapshot.source, "未提供")
                              + " · 活动 " + root.display(root.snapshot.activeCount, "—")
                              + " · 归档 " + root.display(root.snapshot.archivedCount, "—")
                              + " · 记录 " + root.display(root.snapshot.recordCount, "—")
                              + " · 更新 " + root.timeOnly(root.snapshot.updatedAt)
                        textFormat: Text.PlainText
                        color: Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.captionFontSize
                        wrapMode: Text.Wrap
                    }
                    Text {
                        visible: root.display(root.snapshot.sourceReason, "") !== ""
                        Layout.fillWidth: true
                        text: "来源说明：" + root.display(root.snapshot.sourceReason, "")
                        textFormat: Text.PlainText
                        color: Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.captionFontSize
                        wrapMode: Text.Wrap
                    }
                    Text {
                        visible: root.display(root.snapshot.catalogDiagnostic, "") !== ""
                        Layout.fillWidth: true
                        text: "目录诊断：" + root.display(root.snapshot.catalogDiagnostic, "")
                        textFormat: Text.PlainText
                        color: Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.captionFontSize
                        wrapMode: Text.Wrap
                    }
                }
                PaperCard {
                    objectName: "homeSecondaryMetrics"
                    Layout.fillWidth: true
                    Text {
                        text: "学习指标"
                        textFormat: Text.PlainText
                        color: Theme.ink
                        font.family: Theme.fontFamily
                        font.pixelSize: 20
                        font.weight: Font.DemiBold
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: width >= 820 ? 2 : 1
                        columnSpacing: 12
                        rowSpacing: 10
                        Repeater {
                            model: root.list(root.snapshot.metrics)
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: metricColumn.implicitHeight + 22
                                radius: Theme.cardRadius
                                color: Theme.paper
                                border.color: Theme.line
                                border.width: 1
                                ColumnLayout {
                                    id: metricColumn
                                    anchors.fill: parent
                                    anchors.margins: 11
                                    spacing: 4
                                    Text {
                                        text: root.display(modelData.label, "指标")
                                        textFormat: Text.PlainText
                                        color: Theme.muted
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.captionFontSize
                                    }
                                    Text {
                                        text: root.display(modelData.value, "未知")
                                        textFormat: Text.PlainText
                                        color: Theme.ink
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.sectionFontSize
                                        font.weight: Font.DemiBold
                                        wrapMode: Text.Wrap
                                    }
                                    Text {
                                        visible: root.display(modelData.detail, "") !== ""
                                        text: root.display(modelData.detail, "")
                                        textFormat: Text.PlainText
                                        color: Theme.muted
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.captionFontSize
                                        wrapMode: Text.Wrap
                                        Layout.fillWidth: true
                                    }
                                }
                            }
                        }
                    }
                    Text {
                        visible: root.list(root.snapshot.metrics).length === 0
                        Layout.fillWidth: true
                        text: "当前没有额外指标。"
                        textFormat: Text.PlainText
                        color: Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.captionFontSize
                    }
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: width >= 940 ? 2 : 1
                    columnSpacing: 14
                    rowSpacing: 14
                    Repeater {
                        model: [
                            { title: "下一步建议", values: root.list(root.snapshot.todos), empty: "当前没有建议事项。", tone: Theme.paper },
                            { title: "遗忘风险", values: root.list(root.snapshot.memoryRisks), empty: "当前没有遗忘风险数据。", tone: Theme.pinkSoft },
                            { title: "掌握预警", values: root.list(root.snapshot.bktAlerts), empty: "当前没有掌握预警数据。", tone: Theme.peach }
                        ]
                        delegate: PaperCard {
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.minimumWidth: 280
                            paperColor: modelData.tone
                            Text {
                                text: modelData.title
                                textFormat: Text.PlainText
                                color: Theme.ink
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.bodyFontSize
                                font.weight: Font.DemiBold
                            }
                            Repeater {
                                model: modelData.values
                                delegate: ColumnLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: 3
                                    Text {
                                        text: root.display(modelData.title, "")
                                        textFormat: Text.PlainText
                                        color: Theme.ink
                                        font.family: Theme.fontFamily
                                        font.pixelSize: 16
                                        font.weight: Font.DemiBold
                                        wrapMode: Text.Wrap
                                    }
                                    Text {
                                        visible: root.display(modelData.detail, "") !== ""
                                        text: root.display(modelData.detail, "")
                                        textFormat: Text.PlainText
                                        color: Theme.muted
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.captionFontSize
                                        wrapMode: Text.Wrap
                                    }
                                }
                            }
                            Text {
                                visible: root.list(modelData.values).length === 0
                                Layout.fillWidth: true
                                text: modelData.empty
                                textFormat: Text.PlainText
                                color: Theme.muted
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.captionFontSize
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                }
                PaperCard {
                    objectName: "homeRecentRecords"
                    Layout.fillWidth: true
                    paperColor: Theme.paper
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: "最近学习记录"
                            textFormat: Text.PlainText
                            color: Theme.ink
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.bodyFontSize
                            font.weight: Font.DemiBold
                        }
                        Text {
                            text: root.list(root.snapshot.recentRecords).length + " 条"
                            textFormat: Text.PlainText
                            color: Theme.muted
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.captionFontSize
                        }
                    }
                    Repeater {
                        model: root.list(root.snapshot.recentRecords)
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 10
                            Text {
                                text: root.display(modelData.date, "")
                                textFormat: Text.PlainText
                                color: Theme.muted
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.captionFontSize
                                Layout.preferredWidth: 92
                            }
                            Text {
                                text: root.display(modelData.subject, "")
                                textFormat: Text.PlainText
                                color: Theme.ink
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.captionFontSize
                                font.weight: Font.DemiBold
                                Layout.preferredWidth: 130
                                elide: Text.ElideRight
                            }
                            Text {
                                Layout.fillWidth: true
                                text: root.display(modelData.detail, "")
                                textFormat: Text.PlainText
                                color: Theme.muted
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.captionFontSize
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                    Text {
                        visible: root.list(root.snapshot.recentRecords).length === 0
                        Layout.fillWidth: true
                        text: "当前没有活动学科的近期记录。"
                        textFormat: Text.PlainText
                        color: Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.captionFontSize
                    }
                }
                HomeSummaryPane {
                    Layout.fillWidth: true
                    bridge: root.bridge ? root.bridge.dailySummary : null
                    visible: !!bridge
                }
            }
            Item { Layout.preferredHeight: 4 }
        }
    }
}
