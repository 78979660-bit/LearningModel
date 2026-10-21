import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "../components"

ScrollView {
    id: root
    objectName: "practicePane"
    required property var workspace
    readonly property var data: practiceResultSnapshot.value
    readonly property var options: practiceOptionsSnapshot.value
    readonly property var preview: workspace ? workspace.practicePreview : ({})
    readonly property var subjects: (practiceCatalogSnapshot.value.catalog || {}).subjects || []
    readonly property bool initialDataReady: !workspace || (practiceOptionsSnapshot.ready && practiceCatalogSnapshot.ready)
                                            || !!workspace.error
    SnapshotView {
        id: practiceResultSnapshot
        source: root.workspace
        dataProperty: "practiceData"
        pageVisible: root.visible
    }
    SnapshotView {
        id: practiceOptionsSnapshot
        objectName: "practiceOptionsSnapshot"
        source: root.workspace
        dataProperty: "practiceOptions"
        pageVisible: root.visible
    }
    SnapshotView {
        id: practiceCatalogSnapshot
        objectName: "practiceCatalogSnapshot"
        source: root.workspace
        dataProperty: "planData"
        pageVisible: root.visible
    }
    readonly property var templates: (options.templates || []).filter(t => t.localAllowed && (!t.subject || t.subject === subject.currentText))
    property bool ready: false
    contentWidth: availableWidth
    contentHeight: body.implicitHeight + 20
    clip: true

    function invalidate() { if (ready && workspace) workspace.invalidatePreview("practice") }
    function payload() {
        return {subject: subject.currentText, template_id: templateChoice.currentValue || "",
            topic: topic.text, target_difficulty: difficulty.value, question_count: count.value,
            title: paperTitle.text, homework_line: homework.text,
            kind: promptKind.currentValue}
    }
    Component.onCompleted: { if (workspace) workspace.invalidatePreview("practice"); ready = true }
    ColumnLayout {
        id: body
        width: root.availableWidth
        spacing: 14
        PaperCard {
            Layout.fillWidth: true
            BodyText { text: "本地练习卷"; font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
            BodyText { visible: false; text: ""; color: Theme.muted }
            BodyText { visible: root.options.status === "unavailable"; text: String(root.options.detail || ""); color: Theme.warning }
            BodyText { text: "学科" }
            FormChoice { id: subject; objectName: "practiceSubject"; accessibleLabel: "练习学科"; model: root.subjects.map(s => s.name); enabled: root.workspace && !root.workspace.busy; onActivated: root.invalidate() }
            BodyText { text: "题库模板" }
            FormChoice { id: templateChoice; objectName: "practiceTemplate"; accessibleLabel: "练习题库模板"; model: root.templates; textRole: "name"; valueRole: "id"; enabled: root.workspace && !root.workspace.busy; onActivated: root.invalidate() }
            BodyText { text: "知识点筛选（可留空）" }
            FormField { id: topic; objectName: "practiceTopic"; accessibleLabel: "练习知识点筛选"; enabled: root.workspace && !root.workspace.busy; onTextEdited: root.invalidate() }
            BodyText { text: "目标难度（0–100）" }
            SpinBox { id: difficulty; objectName: "practiceDifficulty"; Accessible.name: "练习目标难度"; from: 0; to: 100; value: 55; editable: true; enabled: root.workspace && !root.workspace.busy; onValueModified: root.invalidate() }
            BodyText { text: "题量" }
            SpinBox { id: count; objectName: "practiceCount"; Accessible.name: "练习题量"; from: 1; to: Number(root.options.questionCountMax || 30); value: 2; editable: true; enabled: root.workspace && !root.workspace.busy; onValueModified: root.invalidate() }
            FormField { id: paperTitle; objectName: "practiceTitle"; accessibleLabel: "练习卷标题"; placeholderText: "卷面标题（可留空）"; enabled: root.workspace && !root.workspace.busy; onTextEdited: root.invalidate() }
            ActionButton {
                objectName: "generateLocalPDF"
                text: "生成本地题目卷与答案卷"
                primary: true
                enabled: root.workspace && !root.workspace.busy && subject.currentText.length > 0 && !!templateChoice.currentValue
                onClicked: root.workspace.run("practice.generate", root.payload())
            }
        }
        PaperCard {
            Layout.fillWidth: true
            BodyText { text: "练习提示词与计划上下文"; font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
            BodyText { visible: false; text: ""; color: Theme.muted }
            BodyText { text: "从已保存计划选取作业（可选）" }
            FormChoice {
                objectName: "practicePlanItem"
                accessibleLabel: "已保存的计划作业"
                textRole: "itemText"
                model: root.workspace ? ((root.workspace.planData.currentPlan || {}).items || []).filter(i => i.itemType === "result") : []
                enabled: root.workspace && !root.workspace.busy
                onActivated: { homework.text = currentText; root.invalidate() }
            }
            FormArea { id: homework; objectName: "practiceHomework"; accessibleLabel: "练习生成上下文"; placeholderText: "填写需要生成练习的作业上下文，或使用上方模板参数"; enabled: root.workspace && !root.workspace.busy; onTextChanged: root.invalidate() }
            FormChoice {
                id: promptKind; objectName: "practicePromptKind"; accessibleLabel: "提示词类型"
                textRole: "label"; valueRole: "value"
                model: [{label:"计划作业提示词",value:"practice"},{label:"模拟考试提示词",value:"mock_exam"},{label:"复制计划练习上下文",value:"plan_context"}]
                enabled: root.workspace && !root.workspace.busy; onActivated: root.invalidate()
            }
            ActionButton {
                objectName: "preparePracticePrompt"
                text: "生成并预览提示词"
                enabled: root.workspace && !root.workspace.busy && subject.currentText.length > 0
                onClicked: root.workspace.run("practice.prompt", root.payload())
            }
            BodyText { objectName: "practicePreviewDetail"; text: String(root.preview.detail || ""); visible: text.length > 0; color: root.preview.status === "error" ? Theme.warning : Theme.muted }
            FormArea {
                objectName: "practicePromptPreview"
                accessibleLabel: "可复制的完整练习提示词"
                visible: String(root.preview.prompt || "").length > 0
                text: String(root.preview.prompt || "")
                readOnly: true
            }
            Flow {
                Layout.fillWidth: true
                spacing: 8
                ActionButton { objectName: "copyPracticePrompt"; text: "复制提示词／上下文"; enabled: root.workspace && !root.workspace.busy && !!root.preview.token && !!root.preview.prompt; onClicked: root.workspace.copyPrompt(root.preview.token) }
            }
        }
        PaperCard {
            Layout.fillWidth: true
            visible: !!root.data.status
            BodyText { text: "生成结果 · " + String(root.data.status || ""); font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
            BodyText { text: String(root.data.detail || "") + (root.data.questionCount !== undefined ? "\n实际题量：" + root.data.questionCount : "") + "\n" + (root.data.warnings || []).join("\n"); color: root.data.status === "error" ? Theme.warning : Theme.ink }
            BodyText { text: String(root.data.questionFile || "") + "\n" + String(root.data.answerFile || "") + "\n" + String(root.data.manifestFile || ""); font.pixelSize: Theme.captionFontSize }
            Flow {
                Layout.fillWidth: true
                spacing: 8
                ActionButton { text: "打开题目卷"; visible: !!root.data.questionToken; enabled: root.workspace && !root.workspace.busy; onClicked: root.workspace.openArtifact(root.data.questionToken) }
                ActionButton { text: "打开答案卷"; visible: !!root.data.answerToken; enabled: root.workspace && !root.workspace.busy; onClicked: root.workspace.openArtifact(root.data.answerToken) }
                ActionButton { text: "打开生成记录"; visible: !!root.data.manifestToken; enabled: root.workspace && !root.workspace.busy; onClicked: root.workspace.openArtifact(root.data.manifestToken) }
            }
        }
        Item { implicitHeight: 8 }
    }
}
