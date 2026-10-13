import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "../components"

ScrollView {
    id: root
    objectName: "ojPane"
    required property var workspace
    readonly property var data: ojSnapshot.value
    readonly property bool initialDataReady: !workspace || ojSnapshot.ready || !!workspace.error
    SnapshotView {
        id: ojSnapshot
        objectName: "ojSnapshot"
        source: root.workspace
        dataProperty: "ojData"
        pageVisible: root.visible
    }
    readonly property var problems: data.problems || []
    readonly property var topics: data.topics || []
    readonly property var selected: problems[problemChoice.currentIndex] || ({})
    property var selectedTopicKeys: []
    readonly property var resultOptions: [
        {label:"通过",value:"accepted"},{label:"部分通过",value:"partial"},{label:"答案错误",value:"wrong"},
        {label:"运行错误",value:"runtime_error"},{label:"超时",value:"time_limit"},{label:"超内存",value:"memory_limit"},
        {label:"编译错误",value:"compile_error"},{label:"放弃",value:"abandoned"}]
    contentWidth: availableWidth
    contentHeight: body.implicitHeight + 20
    clip: true

    function attemptPayload() {
        return {problem_key: selected.problem_key, attempted_at: attemptedAt.text,
            result: result.currentValue, duration_seconds: Number(seconds.text),
            independence: independence.currentValue, hint_level: hints.currentValue,
            error_type: result.currentValue === "accepted" ? "none" : errors.currentValue,
            notes: notes.text, source_attempt_key: attemptKey.text.trim() || null}
    }
    Connections { target: root.workspace; function onDataChanged() { root.selectedTopicKeys = [] } }
    ColumnLayout {
        id: body
        width: root.availableWidth
        spacing: 14
        PaperCard {
            Layout.fillWidth: true
            BodyText { text: "OJ 题目与提交"; font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
            BodyText { visible: false; text: ""; color: Theme.muted }
            BodyText { text: "注册或更新题目"; font.weight: Font.DemiBold }
            FormField { id: platform; objectName: "ojPlatform"; accessibleLabel: "OJ 来源代码"; placeholderText: "来源代码"; enabled: root.workspace && !root.workspace.busy }
            FormField { id: externalId; objectName: "ojExternalId"; accessibleLabel: "来源题号"; placeholderText: "来源题号"; enabled: root.workspace && !root.workspace.busy }
            FormField { id: title; objectName: "ojProblemTitle"; accessibleLabel: "题目标题"; placeholderText: "题目标题"; enabled: root.workspace && !root.workspace.busy }
            FormField { id: sourceUrl; objectName: "ojProblemUrl"; accessibleLabel: "来源 URL"; placeholderText: "来源 URL（可空，不访问）"; enabled: root.workspace && !root.workspace.busy }
            ActionButton {
                objectName: "saveOJProblem"
                text: "保存题目身份"
                enabled: root.workspace && !root.workspace.busy && platform.text.trim().length > 0 && externalId.text.trim().length > 0 && title.text.trim().length > 0
                onClicked: root.workspace.run("oj.problem", {source_key: platform.text, external_problem_key: externalId.text, title: title.text, source_url: sourceUrl.text})
            }
        }
        PaperCard {
            Layout.fillWidth: true
            BodyText { text: "选择题目与显式知识点映射"; font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
            FormChoice {
                id: problemChoice; objectName: "ojProblemChoice"; accessibleLabel: "OJ 题目"
                textRole: "title"
                model: root.problems
                enabled: root.workspace && !root.workspace.busy
                onActivated: { root.selectedTopicKeys = []; mappingReviewed.checked = false; attemptReviewed.checked = false }
            }
            BodyText { text: root.selected.problem_key ? "稳定身份：" + root.selected.problem_key : "尚无题目，请先注册。"; font.pixelSize: Theme.captionFontSize }
            BodyText { text: "已保存映射：" + ((root.selected.mappings || []).map(m => m.topic_key).join("、") || "未映射") + "\n尝试 " + Number(root.selected.attempt_count || 0) + " 次 · 最新 " + String(root.selected.latest_result || "未提交") + " · 最佳 " + String(root.selected.best_result || "未提交"); font.pixelSize: Theme.captionFontSize }
            BodyText {
                text: "独立通过：" + (((root.selected.history || {}).summary || {}).ever_independent_accepted ? "是" : "否") + " · 首次通过序号：" + String(((root.selected.history || {}).summary || {}).attempts_to_first_accepted || "未通过") + " · 最近耗时改善：" + String(((root.selected.history || {}).summary || {}).recent_duration_improvement_seconds === null || ((root.selected.history || {}).summary || {}).recent_duration_improvement_seconds === undefined ? "不足两次" : ((root.selected.history || {}).summary || {}).recent_duration_improvement_seconds + " 秒")
                font.pixelSize: Theme.captionFontSize
            }
            Repeater {
                model: root.topics
                CheckBox {
                    required property var modelData
                    required property int index
                    objectName: "oj-topic-" + index
                    Layout.fillWidth: true
                    text: modelData.subject_name + " / " + modelData.module_name + " / " + modelData.topic_name
                    Accessible.name: "映射知识点：" + text
                    enabled: root.workspace && !root.workspace.busy && !!root.selected.problem_key
                    checked: root.selectedTopicKeys.indexOf(modelData.topic_key) >= 0
                    onClicked: {
                        const next = root.selectedTopicKeys.filter(key => key !== modelData.topic_key)
                        if (checked) next.push(modelData.topic_key)
                        root.selectedTopicKeys = next
                        mappingReviewed.checked = false
                    }
                    contentItem: Text {
                        text: parent.text; font: parent.font; color: Theme.ink
                        textFormat: Text.PlainText; wrapMode: Text.Wrap
                        leftPadding: parent.indicator.width + parent.spacing
                    }
                }
            }
            CheckBox { id: mappingReviewed; objectName: "ojMappingReviewed"; text: "确认以上选择是明确的知识点映射"; Accessible.name: text; enabled: root.workspace && !root.workspace.busy }
            ActionButton {
                objectName: "saveOJMapping"
                text: "确认并保存映射"
                primary: true
                enabled: root.workspace && !root.workspace.busy && !!root.selected.problem_key && root.selectedTopicKeys.length > 0 && mappingReviewed.checked
                onClicked: root.workspace.run("oj.mapping", {problem_key: root.selected.problem_key, topic_keys: root.selectedTopicKeys})
            }
        }
        PaperCard {
            Layout.fillWidth: true
            BodyText { text: "追加一次实际提交"; font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
            BodyText { text: "提交时间（带时区的 ISO-8601）" }
            FormField { id: attemptedAt; objectName: "ojAttemptedAt"; accessibleLabel: "提交时间"; text: new Date().toISOString(); enabled: root.workspace && !root.workspace.busy; onTextEdited: attemptReviewed.checked = false }
            BodyText { text: "提交结果" }
            FormChoice { id: result; objectName: "ojResult"; accessibleLabel: "提交结果"; textRole: "label"; valueRole: "value"; model: root.resultOptions; enabled: root.workspace && !root.workspace.busy; onActivated: attemptReviewed.checked = false }
            BodyText { text: "耗时（正整数秒）" }
            FormField { id: seconds; objectName: "ojSeconds"; accessibleLabel: "提交耗时，秒"; validator: IntValidator { bottom: 1; top: 2147483647 } enabled: root.workspace && !root.workspace.busy; onTextEdited: attemptReviewed.checked = false }
            BodyText { text: "独立性" }
            FormChoice {
                id: independence; objectName: "ojIndependence"; accessibleLabel: "提交独立性"; textRole: "label"; valueRole: "value"
                model: [{label:"独立完成",value:"independent"},{label:"有指导",value:"guided"},{label:"参考答案",value:"copied"}]
                enabled: root.workspace && !root.workspace.busy; onActivated: attemptReviewed.checked = false
            }
            BodyText { text: "提示程度" }
            FormChoice {
                id: hints; objectName: "ojHints"; accessibleLabel: "提示程度"; textRole: "label"; valueRole: "value"
                model: [{label:"无提示",value:"none"},{label:"概念提示",value:"concept"},{label:"伪代码",value:"pseudocode"},{label:"完整解法",value:"solution"}]
                enabled: root.workspace && !root.workspace.busy; onActivated: attemptReviewed.checked = false
            }
            BodyText { text: "错误类型"; visible: result.currentValue !== "accepted" }
            FormChoice {
                id: errors; objectName: "ojErrorType"; accessibleLabel: "错误类型"; textRole: "label"; valueRole: "value"
                model: [{label:"题意误解",value:"misunderstood"},{label:"算法",value:"algorithm"},{label:"实现",value:"implementation"},{label:"边界",value:"edge_case"},{label:"复杂度",value:"complexity"},{label:"语法",value:"syntax"},{label:"运行",value:"runtime"},{label:"未判定",value:"unknown"}]
                visible: result.currentValue !== "accepted"
                enabled: root.workspace && !root.workspace.busy; onActivated: attemptReviewed.checked = false
            }
            FormField { id: notes; objectName: "ojNotes"; accessibleLabel: "提交备注"; placeholderText: "提交备注（可空）"; enabled: root.workspace && !root.workspace.busy; onTextEdited: attemptReviewed.checked = false }
            FormField { id: attemptKey; objectName: "ojAttemptKey"; accessibleLabel: "提交幂等键"; placeholderText: "导入或重试时的幂等键（可空）"; enabled: root.workspace && !root.workspace.busy; onTextEdited: attemptReviewed.checked = false }
            CheckBox { id: attemptReviewed; objectName: "ojAttemptReviewed"; text: "我已复核此次提交的结果、耗时和提示信息"; Accessible.name: text; enabled: root.workspace && !root.workspace.busy }
            ActionButton {
                objectName: "saveOJAttempt"
                text: "确认追加提交"
                primary: true
                enabled: root.workspace && !root.workspace.busy && !!root.selected.problem_key && (root.selected.mappings || []).length > 0 && seconds.text.length > 0 && seconds.acceptableInput && attemptReviewed.checked
                onClicked: root.workspace.run("oj.attempt", root.attemptPayload())
            }
            BodyText { text: "提交历史"; font.weight: Font.DemiBold }
            Repeater {
                model: (root.data.attempts || []).filter(a => a.problem_key === root.selected.problem_key)
                BodyText {
                    required property var modelData
                    text: modelData.attempted_at + " · " + modelData.result + " · " + modelData.duration_seconds + " 秒\n" + modelData.independence + " / " + modelData.hint_level + " / " + modelData.error_type + " · " + String(modelData.notes || "")
                    font.pixelSize: Theme.captionFontSize
                }
            }
        }
        Item { implicitHeight: 8 }
    }
}
