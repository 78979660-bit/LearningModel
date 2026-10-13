import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "../components"
import "."

Item {
    id: root
    objectName: "knowledgePage"
    required property var bridge
    readonly property var work: bridge ? bridge.insights : null
    readonly property var knowledgeData: knowledgeSnapshot.value
    SnapshotView {
        id: knowledgeSnapshot
        objectName: "knowledgeSnapshot"
        source: root.work
        dataProperty: "knowledgeData"
        pageVisible: root.visible
    }
    readonly property bool initialDataReady: !work || Object.keys(knowledgeData).length > 0 || !!work.error
    readonly property bool preloadReady: initialDataReady && (!work || !reconciliationTimer.running || !!work.error)
    readonly property var graphData: knowledgeData.graph || ({})
    readonly property var subjects: list(knowledgeData.subjects)
    readonly property var graphNodes: list(graphData.nodes)
    readonly property var selectedNode: findNode(selectedModuleKey)
    readonly property var selectedDetails: graphData.details
            ? graphData.details[selectedModuleKey] : null
    readonly property bool busy: work ? work.busy : false
    readonly property string errorMessage: work ? String(work.error || "") : ""
    readonly property string statusMessage: work ? String(work.message || "") : ""
    property string selectedSubjectKey: ""
    property string selectedModuleKey: ""
    property string activeTab: "graph"
    property bool detailsVisible: true

    function list(value) {
        if (!value) return []
        if (Array.isArray(value)) return value
        if (typeof value.length === "number") return value
        return []
    }

    function display(value, fallback) {
        if (value === undefined || value === null || value === "")
            return fallback === undefined ? "未记录" : fallback
        return String(value)
    }

    function findNode(key) {
        return graphNodes.find(function(node) { return String(node.key) === String(key) }) || null
    }

    function percent(value) {
        if (value === undefined || value === null || value === "") return "暂无评估"
        const number = Number(value)
        return Number.isFinite(number) ? Math.round(number * 100) + "%" : "暂无评估"
    }

    function stateLabel(value) {
        const labels = {
            unlearned: "尚未学习",
            partial: "部分覆盖",
            learned_unassessed: "已覆盖 · 待评估",
            learned_assessed: "已评估",
            archived: "已归档",
            recent_failure: "近期失败",
            insufficient_evidence: "证据不足",
            overdue_review: "间隔过长",
            stable: "状态稳定",
            sufficient: "证据充足"
        }
        return labels[String(value)] || display(value, "未知状态")
    }

    function reasonLabel(value) {
        const labels = {
            course_not_started_no_observations: "课程尚未开始，且暂无有效作答",
            recent_uncorrected_failure: "近期错误尚未得到后续正确作答",
            no_valid_observations: "尚无有效作答观察",
            evidence_confidence_below_threshold: "证据置信度低于当前要求",
            recall_below_target: "当前回忆概率低于目标",
            recommended_date_passed: "建议复习日期已过",
            no_action_condition: "未触发需要处理的条件"
        }
        return labels[String(value)] || display(value, "未记录判定理由")
    }

    function actionLabel(value) {
        const labels = {
            learn_then_assess: "先学习，再完成评估",
            retry_failed_problem: "重做近期错题",
            complete_independent_diagnostic: "完成一次独立诊断练习",
            review_now: "安排复习",
            continue_schedule: "按当前间隔继续学习"
        }
        return labels[String(value)] || display(value, "未记录建议动作")
    }

    function layerLabel(value) {
        const labels = { rules: "分类依据", evidence: "学习证据", model: "掌握与复习估计", workflow: "预警流程" }
        return labels[String(value)] || display(value, "说明")
    }

    function factLabel(fact) {
        if (fact.code === "reason_codes") return "判定理由"
        return display(fact.label, "事实")
    }

    function prerequisiteText(value) {
        const states = ["unlearned", "partial", "learned_unassessed", "learned_assessed",
                        "archived", "recent_failure", "insufficient_evidence", "overdue_review", "stable"]
        return String(value || "").split("；").map(function(entry) {
            const separator = entry.lastIndexOf(": ")
            if (separator < 0) return entry
            let status = entry.slice(separator + 2)
            let suffix = ""
            if (status.endsWith("（封存历史）")) {
                status = status.slice(0, -6)
                suffix = "（封存历史）"
            }
            return entry.slice(0, separator + 2) + (states.indexOf(status) >= 0 ? stateLabel(status) : status) + suffix
        }).join("；")
    }

    function auditFactValue(fact) {
        const value = display(fact.value, "未记录")
        if (fact.code === "primary_state") return stateLabel(value)
        if (fact.code === "reason_codes") return value.split("、").map(reasonLabel).join("；")
        if (fact.code === "recommended_action") return actionLabel(value)
        if (fact.code === "prerequisite_states") return prerequisiteText(value)
        if (fact.code === "trace_coverage") {
            const labels = { traced: "可追溯", partial_legacy: "部分旧记录无法追溯", no_recorded_contribution: "没有可追溯贡献" }
            return labels[value] || value
        }
        return value
    }

    function businessFacts(layer) {
        const allowed = {
            primary_state: true, reason_codes: true, recommended_action: true,
            observation_count: true, exercise_count: true, evidence_confidence: true,
            last_observation_date: true, recent_error_title: true, recent_error_date: true,
            recent_error_correctness: true, recent_error_cause: true, prerequisite_states: true,
            mastery_point: true, last_review_date: true, recall_probability: true,
            target_recall: true, recommended_date: true, trace_coverage: true
        }
        return list(layer && layer.facts).filter(function(fact) { return !!allowed[String(fact.code)] })
    }

    function refresh() {
        if (!work || busy) return
        const payload = selectedSubjectKey.length > 0 ? { subjectKey: selectedSubjectKey } : {}
        work.run("knowledge.load", payload)
    }

    function reconcileData() {
        const dataKey = String(knowledgeData.selectedSubjectKey || "")
        const nextSubjects = list(knowledgeData.subjects)
        const subjectExists = nextSubjects.some(function(subject) { return String(subject.key) === selectedSubjectKey })
        if (dataKey.length > 0) selectedSubjectKey = dataKey
        else if (!subjectExists) selectedSubjectKey = nextSubjects.length > 0 ? String(nextSubjects[0].key) : ""

        const nextNodes = list(knowledgeData.graph && knowledgeData.graph.nodes)
        const currentExists = nextNodes.some(function(node) { return String(node.key) === selectedModuleKey })
        if (!currentExists) selectedModuleKey = nextNodes.length > 0 ? String(nextNodes[0].key) : ""
    }

    function selectSubject(key) {
        const value = String(key || "")
        if (value.length === 0 || value === selectedSubjectKey || busy) return
        selectedSubjectKey = value
        selectedModuleKey = ""
        work.run("knowledge.load", { subjectKey: value })
    }

    property bool pageReady: false
    property bool entered: false
    function enterPage() {
        if (!visible || entered || !work) return
        entered = true
        work.loadKnowledgeForPage()
    }
    Component.onCompleted: { pageReady = true; reconciliationTimer.restart(); enterPage() }
    onVisibleChanged: {
        if (!visible) entered = false
        else if (pageReady) { reconciliationTimer.restart(); enterPage() }
    }
    onKnowledgeDataChanged: reconciliationTimer.restart()
    Connections {
        target: root.work
        function onStateChanged() {
            if (root.pageReady && !root.work.busy) reconciliationTimer.restart()
        }
    }

    Timer {
        id: reconciliationTimer
        interval: 1
        repeat: false
        onTriggered: root.reconcileData()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.width < 820 || root.height < 700 ? 16 : Theme.pageMargin
        spacing: root.height < 700 ? 8 : Theme.panelSpacing

        RowLayout {
            Layout.fillWidth: true
            Layout.maximumWidth: 630
            Layout.minimumHeight: root.height < 700 ? 64 : 88
            spacing: 12
            ColumnLayout {
                spacing: 3
                Text {
                    text: "知识图谱"
                    textFormat: Text.PlainText
                    color: Theme.ink
                    font.family: Theme.headingFontFamily
                    font.pixelSize: root.width >= 900 ? Theme.pageTitleSize : 36
                    font.weight: Font.DemiBold
                }
                Text {
                    text: ""
                    visible: false
                    textFormat: Text.PlainText
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionFontSize
                }
            }
            FormChoice {
                id: subjectChoice
                objectName: "knowledgeSubjectChoice"
                Layout.minimumWidth: 180
                Layout.maximumWidth: 300
                accessibleLabel: "选择知识图谱学科"
                model: root.subjects
                textRole: "label"
                valueRole: "key"
                currentIndex: {
                    for (let index = 0; index < root.subjects.length; index++) {
                        if (String(root.subjects[index].key) === root.selectedSubjectKey) return index
                    }
                    return -1
                }
                onActivated: root.selectSubject(currentValue)
            }
            BusyIndicator { running: root.busy; opacity: running ? 1 : 0; implicitWidth: 24; implicitHeight: 24 }
            ActionButton {
                objectName: "knowledgeRefreshButton"
                text: ""
                accessibleLabel: "刷新知识图谱与预警数据"
                enabled: !root.busy
                iconSource: Qt.resolvedUrl("../assets/icons/refresh.svg")
                onClicked: root.refresh()
            }
        }

        PaperCard {
            Layout.fillWidth: true
            visible: root.errorMessage.length > 0
            padding: root.height < 700 ? 8 : 11
            paperColor: Theme.paper
            RowLayout {
                Layout.fillWidth: true
                spacing: 9
                Text {
                    Layout.fillWidth: true
                    text: root.display(root.knowledgeData.asOfDate, "") +
                          (root.graphNodes.length > 0 ? " · " + root.graphNodes.length + " 个章节" : "")
                    textFormat: Text.PlainText
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionFontSize
                    wrapMode: Text.Wrap
                }
            }
            Text {
                visible: root.errorMessage.length > 0
                Layout.fillWidth: true
                text: root.errorMessage
                textFormat: Text.PlainText
                color: Theme.warning
                font.family: Theme.fontFamily
                font.pixelSize: Theme.captionFontSize
                wrapMode: Text.Wrap
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: root.height < 700 ? 5 : 8
            ActionButton {
                objectName: "knowledgeGraphTabButton"
                text: "章节图谱"
                primary: root.activeTab === "graph"
                accessibleLabel: "显示章节图谱"
                Accessible.role: Accessible.PageTab
                Accessible.selected: root.activeTab === "graph"
                onClicked: root.activeTab = "graph"
            }
            ActionButton {
                objectName: "knowledgeAlertsTabButton"
                text: "预警与记录（" + root.list(root.knowledgeData.alerts).length + "）"
                primary: root.activeTab === "alerts"
                accessibleLabel: "显示预警和处理记录"
                Accessible.role: Accessible.PageTab
                Accessible.selected: root.activeTab === "alerts"
                onClicked: root.activeTab = "alerts"
            }
            Item { Layout.fillWidth: true }
            ActionButton {
                objectName: "knowledgeDetailsButton"
                visible: root.activeTab === "graph"
                text: root.detailsVisible ? "收起详情" : "章节详情"
                onClicked: root.detailsVisible = !root.detailsVisible
            }
        }

        StackLayout {
            id: pageStack
            objectName: "knowledgeContentStack"
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: root.activeTab === "alerts" ? 1 : 0

            GridLayout {
                id: graphTabGrid
                objectName: "knowledgeGraphTab"
                Layout.fillWidth: true
                Layout.fillHeight: true
                columns: graphTabGrid.width >= 900 && root.detailsVisible ? 2 : 1
                rowSpacing: Theme.panelSpacing
                columnSpacing: Theme.panelSpacing
                GraphPane {
                    id: graphPane
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumWidth: graphTabGrid.columns === 2 ? 550 : 0
                    Layout.preferredWidth: graphTabGrid.columns === 2 ? graphTabGrid.width - 310 : graphTabGrid.width
                    graph: root.graphData
                    selectedKey: root.selectedModuleKey
                    busy: root.busy
                    onNodeSelected: (key) => { root.selectedModuleKey = key }
                }

                PaperCard {
                    id: topicDetailCard
                    objectName: "knowledgeTopicDetail"
                    Layout.fillWidth: true
                    Layout.fillHeight: false
                    Layout.alignment: Qt.AlignTop
                    Layout.minimumWidth: graphTabGrid.columns === 2 ? 280 : 0
                    Layout.maximumWidth: graphTabGrid.columns === 2 ? 310 : Infinity
                    Layout.preferredWidth: graphTabGrid.columns === 2 ? 300 : graphTabGrid.width
                    Layout.preferredHeight: topicEvidenceToggle.checked ? Math.min(460, root.height * 0.5) : 155
                    Layout.minimumHeight: 135
                    padding: root.height < 700 ? 8 : 12
                    paperColor: graphTabGrid.columns === 2 ? Theme.peach : Theme.card
                    visible: root.detailsVisible

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.alignment: Qt.AlignTop
                        spacing: root.height < 700 ? 4 : 7
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Text {
                                Layout.fillWidth: true
                                text: root.selectedDetails ? String(root.selectedDetails.label || root.selectedNode.label) :
                                      String(root.selectedNode ? root.selectedNode.label : "章节详情")
                                textFormat: Text.PlainText
                                color: Theme.ink
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.bodyFontSize
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                            }
                            StatusPill {
                                text: root.selectedNode ? root.stateLabel(root.selectedNode.state) : ""
                                tone: Theme.accent
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            opacity: root.selectedNode ? 1 : 0
                            text: "覆盖 " + String(root.selectedNode ? root.selectedNode.coverageCount || 0 : 0) +
                                  " / " + String(root.selectedNode ? root.selectedNode.topicCount || 0 : 0) +
                                  " 个知识点 · 章节掌握 " + root.percent(root.selectedNode ? root.selectedNode.masteryPoint : null) +
                                  " · 证据置信度 " + root.percent(root.selectedNode ? root.selectedNode.evidenceConfidence : null)
                            textFormat: Text.PlainText
                            color: Theme.muted
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.captionFontSize
                            wrapMode: Text.Wrap
                        }
                        CheckBox {
                            id: topicEvidenceToggle
                            objectName: "knowledgeTopicEvidenceToggle"
                            Layout.fillWidth: true
                            text: "知识点详情"
                            enabled: root.selectedNode !== null
                            Accessible.name: text
                        }
                        ScrollView {
                            id: detailScroll
                            objectName: "knowledgeTopicDetailScroll"
                            visible: topicEvidenceToggle.checked
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.minimumHeight: 0
                            Layout.preferredHeight: graphTabGrid.columns === 2 ? root.height : (root.height < 700 ? 42 : 100)
                            Layout.maximumHeight: graphTabGrid.columns === 2 ? root.height : (root.height < 700 ? 62 : 180)
                            clip: true
                            contentWidth: availableWidth
                            ScrollBar.vertical.policy: ScrollBar.AsNeeded
                            Accessible.name: "所选章节的知识点证据与说明，可在此滚动查看"
                            ColumnLayout {
                                width: Math.max(0, detailScroll.availableWidth - 8)
                                spacing: 6
                                Text {
                                    visible: root.list(root.selectedDetails && root.selectedDetails.topics).length === 0
                                    Layout.fillWidth: true
                                    text: "当前章节没有可关联的知识点明细。"
                                    textFormat: Text.PlainText
                                    color: Theme.muted
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.captionFontSize
                                }
                                Repeater {
                                    model: root.list(root.selectedDetails && root.selectedDetails.topics)
                                    delegate: ColumnLayout {
                                        id: topicDetail
                                        required property var modelData
                                        Layout.fillWidth: true
                                        spacing: 4
                                        Rectangle {
                                            Layout.fillWidth: true
                                            height: 1
                                            color: Theme.line
                                        }
                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 8
                                            Text {
                                                Layout.fillWidth: true
                                                text: String(topicDetail.modelData.label || "知识点")
                                                textFormat: Text.PlainText
                                                color: Theme.ink
                                                font.family: Theme.fontFamily
                                                font.pixelSize: 16
                                                font.weight: Font.DemiBold
                                                wrapMode: Text.Wrap
                                            }
                                            StatusPill {
                                                text: root.stateLabel(topicDetail.modelData.state)
                                                tone: topicDetail.modelData.state === "recent_failure" ? Theme.warning : Theme.leaf
                                            }
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            text: "掌握 " + root.percent(topicDetail.modelData.masteryPoint) +
                                                  " · 观察 " + String(topicDetail.modelData.observationCount || 0) +
                                                  " · 练习 " + String(topicDetail.modelData.exerciseCount || 0) +
                                                  " · 证据置信度 " + root.percent(topicDetail.modelData.evidenceConfidence) +
                                                  " · 最近观察 " + root.display(topicDetail.modelData.lastObservationDate, "暂无")
                                            textFormat: Text.PlainText
                                            color: Theme.muted
                                            font.family: Theme.fontFamily
                                            font.pixelSize: Theme.captionFontSize
                                            wrapMode: Text.Wrap
                                        }
                                        Text {
                                            visible: root.list(topicDetail.modelData.reasonCodes).length > 0
                                            Layout.fillWidth: true
                                            text: "判定理由：" + root.list(topicDetail.modelData.reasonCodes).map(root.reasonLabel).join("；") +
                                                  " · 建议动作：" + root.actionLabel(topicDetail.modelData.recommendedAction)
                                            textFormat: Text.PlainText
                                            color: Theme.muted
                                            font.family: Theme.fontFamily
                                            font.pixelSize: Theme.captionFontSize
                                            wrapMode: Text.Wrap
                                        }
                                Repeater {
                                    model: root.list(topicDetail.modelData.explanation && topicDetail.modelData.explanation.layers)
                                          .filter(function(layer) { return layer.name !== "workflow" })
                                    delegate: ColumnLayout {
                                        id: topicLayer
                                        required property var modelData
                                        Layout.fillWidth: true
                                        spacing: 3
                                        Text {
                                            Layout.fillWidth: true
                                            text: "依据 · " + root.layerLabel(topicLayer.modelData.name)
                                            textFormat: Text.PlainText
                                            color: Theme.ink
                                            font.family: Theme.fontFamily
                                            font.pixelSize: Theme.captionFontSize
                                            font.weight: Font.DemiBold
                                        }
                                        Repeater {
                                            model: root.businessFacts(topicLayer.modelData)
                                            delegate: BodyText {
                                                required property var modelData
                                                color: Theme.muted
                                                font.pixelSize: Theme.captionFontSize
                                                text: root.factLabel(modelData) + "：" + root.auditFactValue(modelData)
                                            }
                                        }
                                    }
                                }
                                CheckBox {
                                    id: topicAuditToggle
                                    objectName: "knowledgeTopicAuditToggle_" + String(topicDetail.modelData.key || "")
                                    text: "展开规则版本与来源（审计）"
                                    Accessible.name: text
                                }
                                ColumnLayout {
                                    visible: topicAuditToggle.checked
                                    Layout.fillWidth: true
                                    spacing: 5
                                    Text {
                                        Layout.fillWidth: true
                                        text: "解释版本 " + root.display(topicDetail.modelData.explanation && topicDetail.modelData.explanation.version, "未知")
                                        textFormat: Text.PlainText
                                        color: Theme.muted
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.captionFontSize
                                        wrapMode: Text.Wrap
                                    }
                                    Repeater {
                                        model: root.list(topicDetail.modelData.explanation && topicDetail.modelData.explanation.layers)
                                        delegate: ColumnLayout {
                                            id: auditLayer
                                            required property var modelData
                                            Layout.fillWidth: true
                                            spacing: 2
                                            Text {
                                                Layout.fillWidth: true
                                                text: root.layerLabel(auditLayer.modelData.name)
                                                textFormat: Text.PlainText
                                                color: Theme.ink
                                                font.family: Theme.fontFamily
                                                font.pixelSize: Theme.captionFontSize
                                                font.weight: Font.DemiBold
                                            }
                                            Repeater {
                                                model: root.list(auditLayer.modelData.facts)
                                                delegate: BodyText {
                                                    required property var modelData
                                                    color: Theme.muted
                                                    font.pixelSize: Theme.captionFontSize
                                                    text: root.factLabel(modelData) + "：" +
                                                          root.auditFactValue(modelData) + " · 来源/版本 " +
                                                          root.display(modelData.sourceReference, "未知")
                                                }
                                            }
                                        }
                                    }
                                    BodyText {
                                        visible: root.list(topicDetail.modelData.explanation && topicDetail.modelData.explanation.diagnostics).length > 0
                                        color: Theme.muted
                                        font.pixelSize: Theme.captionFontSize
                                        text: "诊断记录：" + root.list(topicDetail.modelData.explanation && topicDetail.modelData.explanation.diagnostics).join("、")
                                    }
                                }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            AlertsPane {
                id: alertsPane
                Layout.fillWidth: true
                Layout.fillHeight: true
                alerts: root.list(root.knowledgeData.alerts)
                work: root.work
                asOfDate: String(root.knowledgeData.asOfDate || "")
            }
        }
    }
}

