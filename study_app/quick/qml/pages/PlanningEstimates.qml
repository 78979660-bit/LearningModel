import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "../components"

PaperCard {
    required property var plan
    objectName: "planEstimateDetails"
    padding: 16
    paperColor: Theme.lavender
    BodyText { text: "考试日期与任务估时"; font.pixelSize: 20; font.weight: Font.DemiBold }
    BodyText { text: "日期可留空；没有可靠时长的任务不会自动占用预算。"; color: Theme.muted }
    GridLayout {
        Layout.fillWidth: true
        columns: width >= 760 ? 2 : 1
        columnSpacing: 14
        rowSpacing: 10
        Repeater {
            model: plan.estimatesCreated ? plan.subjects : []
            delegate: ColumnLayout {
                required property var modelData
                Layout.fillWidth: true
                BodyText {
                    text: modelData.name + " · 考试范围：" + String((modelData.examScope || {}).label || "沿用课程目录")
                    color: Theme.muted
                    font.pixelSize: Theme.captionFontSize
                }
                BodyText { text: modelData.name + "考试日期"; font.pixelSize: Theme.captionFontSize }
                FormField {
                    objectName: "exam-date-" + modelData.name
                    accessibleLabel: modelData.name + "考试日期"
                    placeholderText: "YYYY-MM-DD"
                    text: String((plan.planData.examDates || {})[modelData.name] || "")
                    enabled: plan.workspace && !plan.workspace.busy
                    onTextEdited: {
                        const next = Object.assign({}, plan.examDates)
                        next[modelData.name] = text
                        plan.examDates = next
                        plan.invalidateBudget()
                    }
                }
            }
        }
    }
    BodyText { text: "任务估时"; font.weight: Font.DemiBold }
    Repeater {
        model: plan.estimatesCreated ? (plan.planData.tasks || []) : []
        delegate: RowLayout {
            required property var modelData
            required property int index
            Layout.fillWidth: true
            BodyText {
                Layout.fillWidth: true
                text: modelData.title + " · "
                      + (modelData.estimatedMinutes ? "已有估时：" + modelData.estimatedMinutes + " 分钟"
                                                     : "估时未知，请按实际情况填写")
                font.pixelSize: Theme.captionFontSize
            }
            SpinBox {
                objectName: "estimate-" + index
                Accessible.name: modelData.title + "估时，分钟"
                from: 0
                to: 1440
                editable: true
                value: Number(modelData.estimatedMinutes || 0)
                enabled: plan.workspace && !plan.workspace.busy
                onValueModified: {
                    const next = Object.assign({}, plan.estimates)
                    next[modelData.taskId] = value
                    plan.estimates = next
                    plan.invalidateBudget()
                }
            }
        }
    }
}
