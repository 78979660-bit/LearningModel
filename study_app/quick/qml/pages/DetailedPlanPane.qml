import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "../components"

PaperCard {
    signal archiveRequested()
    required property var plan
    objectName: "detailedPlanContent"
    padding: 22
    paperColor: Theme.paper
    BodyText {
        visible: false; text: ""
        color: Theme.muted
        font.pixelSize: Theme.captionFontSize
    }
    CheckBox {
        id: enhanced
        objectName: "planEnhanced"
        text: "使用已配置的增强模型（可能发送学习上下文）"
        Accessible.name: text
        enabled: plan.workspace && !plan.workspace.busy
        onToggled: plan.invalidatePlan()
    }
    Flow {
        Layout.fillWidth: true
        spacing: 8
        ActionButton {
            objectName: "previewDetailedPlan"
            text: "生成详细计划预览"
            enabled: plan.workspace && !plan.workspace.busy && plan.subjects.length > 0
            onClicked: plan.workspace.run("plan.preview", {scope: plan.selectedSubject || null, enhanced: enhanced.checked})
        }
        ActionButton {
            objectName: "confirmDetailedPlan"
            text: "确认并保存详细计划"
            enabled: plan.workspace && !plan.workspace.busy && !!plan.planDraft.token && planReviewed.checked
            onClicked: plan.workspace.run("plan.confirm", {token: plan.planDraft.token})
        }
        ActionButton {
            objectName: "archivePlan"
            text: "归档当前计划"
            enabled: plan.workspace && !plan.workspace.busy && !!plan.planData.currentPlan
            onClicked: archiveRequested()
        }
    }
    CheckBox {
        id: planReviewed
        objectName: "planReviewed"
        text: "我已复核详细计划，确认保存"
        Accessible.name: text
        enabled: !!plan.planDraft.token && plan.workspace && !plan.workspace.busy
    }
    Connections {
        target: plan.workspace
        function onPreviewsChanged() { planReviewed.checked = false }
    }
    BodyText { visible: !!plan.planDraft.token; text: "计划预览 · " + String(plan.planDraft.source || "本地服务") }
    Repeater {
        model: plan.detailCreated ? (plan.planDraft.items || []) : []
        delegate: BodyText {
            required property var modelData
            text: String(modelData.sectionTitle || modelData.sectionKey || "") + " · " + plan.line(modelData)
            font.pixelSize: Theme.captionFontSize
        }
    }
    Repeater {
        model: plan.detailCreated ? plan.rows(plan.planData.currentPlan) : []
        delegate: ColumnLayout {
            required property var modelData
            Layout.fillWidth: true
            RowLayout {
                Layout.fillWidth: true
                CheckBox {
                    objectName: "plan-check-" + modelData.id
                    checked: !!modelData.checked
                    Accessible.name: "计划任务完成状态：" + plan.line(modelData)
                    visible: modelData.itemType === "check"
                    enabled: plan.workspace && !plan.workspace.busy && modelData.itemType === "check"
                    onClicked: plan.workspace.run("plan.check", {id: modelData.id, checked: checked})
                }
                BodyText { text: String(modelData.sectionTitle || "") + " · " + plan.line(modelData); font.pixelSize: Theme.captionFontSize }
            }
            Flow {
                Layout.fillWidth: true
                visible: modelData.itemType === "result"
                spacing: 8
                ActionButton {
                    objectName: "feedback-correct-" + modelData.id
                    text: "作业完成正确"
                    enabled: plan.workspace && !plan.workspace.busy && !modelData.result
                    onClicked: plan.workspace.run("plan.feedback", {id: modelData.id, correct: true})
                }
                ActionButton {
                    objectName: "feedback-wrong-" + modelData.id
                    text: "作业完成有误"
                    enabled: plan.workspace && !plan.workspace.busy && !modelData.result
                    onClicked: plan.workspace.run("plan.feedback", {id: modelData.id, correct: false})
                }
            }
            BodyText {
                visible: !!modelData.result
                text: "反馈：" + String(modelData.result || "")
                font.pixelSize: Theme.captionFontSize
                color: Theme.muted
            }
        }
    }
    Repeater {
        model: plan.detailCreated ? (plan.planData.dayFeedback || []) : []
        delegate: BodyText {
            required property var modelData
            text: modelData.line + (modelData.complete ? "\n" + modelData.popup : "")
            font.pixelSize: Theme.captionFontSize
        }
    }
    BodyText {
        visible: plan.workspace && plan.workspace.lastResult.score !== undefined
        text: plan.workspace ? "本次作业反馈分数：" + String(plan.workspace.lastResult.score || 0)
              + " · " + String(plan.workspace.lastResult.scoreSource || "")
              + "\n" + String(plan.workspace.lastResult.fallbackReason || "") : ""
        font.pixelSize: Theme.captionFontSize
    }
}
