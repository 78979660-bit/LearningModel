import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "../components"

Item {
    id: root
    objectName: "alertsPane"
    property var alerts: []
    property var work: null
    property string asOfDate: ""
    property string statusFilter: ""
    property var pendingAlert: null
    property string pendingAction: ""
    property string snoozeDate: ""
    readonly property var visibleAlerts: filteredAlerts()

    function list(value) {
        if (!value) return []
        if (Array.isArray(value)) return value
        if (typeof value.length === "number") return value
        return []
    }

    function filteredAlerts() {
        return list(alerts).filter(function(alert) {
            return statusFilter.length === 0 || String(alert.status || "") === statusFilter
        })
    }

    function display(value, fallback) {
        if (value === undefined || value === null || value === "") return fallback === undefined ? "未记录" : fallback
        return String(value)
    }

    function statusLabel(value) {
        const labels = { active: "活动预警", snoozed: "已延后", handled: "已处理", resolved: "已解除" }
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

    function factValue(fact) {
        const value = display(fact.value, "未记录")
        if (fact.code === "primary_state" || fact.code === "alert_type") return typeLabel(value)
        if (fact.code === "alert_status") return statusLabel(value)
        if (fact.code === "reason_codes") return value.split("、").map(reasonLabel).join("；")
        if (fact.code === "recommended_action") return actionLabel(value)
        return value
    }

    function businessFacts(layer) {
        const allowed = {
            primary_state: true, alert_type: true, alert_status: true, reason_codes: true,
            recommended_action: true, observation_count: true, exercise_count: true,
            evidence_confidence: true, last_observation_date: true, recent_error_title: true,
            recent_error_date: true, recent_error_correctness: true, recent_error_cause: true,
            prerequisite_states: true, mastery_point: true, last_review_date: true,
            recall_probability: true, target_recall: true, recommended_date: true
        }
        return list(layer && layer.facts).filter(function(fact) { return !!allowed[String(fact.code)] })
    }

    function auditEventType(value) {
        const labels = { created: "创建", snoozed: "延后", handled: "标记已处理", resolved: "解除", reopened: "重新开启" }
        return labels[String(value)] || display(value, "事件")
    }

    function auditActor(value) {
        return String(value) === "desktop_local_user" ? "本地用户" : display(value, "未记录")
    }

    function typeLabel(value) {
        const labels = { recent_failure: "近期失败", insufficient_evidence: "证据不足", overdue_review: "间隔过长" }
        return labels[String(value)] || display(value, "预警")
    }

    function nextDay(value) {
        const text = String(value || root.asOfDate || "")
        const parts = text.split("-")
        if (parts.length !== 3) return ""
        const parsed = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]))
        if (!Number.isFinite(parsed.getTime())) return ""
        parsed.setDate(parsed.getDate() + 1)
        return String(parsed.getFullYear()).padStart(4, "0") + "-" +
               String(parsed.getMonth() + 1).padStart(2, "0") + "-" +
               String(parsed.getDate()).padStart(2, "0")
    }

    function validSnoozeDate() {
        const value = snoozeDate.trim()
        if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
        const parts = value.split("-").map(Number)
        const parsed = new Date(parts[0], parts[1] - 1, parts[2])
        if (!Number.isFinite(parsed.getTime()) || parsed.getFullYear() !== parts[0] ||
                parsed.getMonth() !== parts[1] - 1 || parsed.getDate() !== parts[2]) return false
        const earliest = String(pendingAlert ? pendingAlert.asOfDate : root.asOfDate || "")
        return earliest.length === 0 || value >= earliest
    }

    function beginAction(alert, action) {
        pendingAlert = alert
        pendingAction = action
        snoozeDate = nextDay(alert.asOfDate)
        actionDialog.open()
    }

    function submitConfirmedAction() {
        if (!work || !pendingAlert) return
        if (pendingAction === "snooze" && !validSnoozeDate()) return
        if (pendingAction === "handle") {
            work.run("alert.handle", { alertId: Number(pendingAlert.id), confirmed: true })
        } else if (pendingAction === "snooze") {
            work.run("alert.snooze", {
                alertId: Number(pendingAlert.id),
                date: snoozeDate.trim(),
                confirmed: true
            })
        }
        actionDialog.close()
    }

    function eventText(event) {
        const transition = (event.fromStatus ? statusLabel(event.fromStatus) : "新建") +
                " → " + statusLabel(event.toStatus)
        const detail = event.detail && Object.keys(event.detail).length > 0
                ? " · 操作详情 " + JSON.stringify(event.detail) : ""
        return display(event.effectiveDate, "") + " · " + auditEventType(event.eventType) +
                " · " + transition + " · 操作者 " + auditActor(event.actor) + detail
    }

    ScrollView {
        id: alertScroll
        objectName: "knowledgeAlertsScroll"
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth
        contentHeight: alertColumn.implicitHeight + 20
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            id: alertColumn
            width: Math.max(0, alertScroll.availableWidth - 8)
            spacing: 12

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Text {
                    Layout.fillWidth: true
                    text: "预警与处理记录"
                    textFormat: Text.PlainText
                    color: Theme.ink
                    font.family: Theme.fontFamily
                    font.pixelSize: 20
                    font.weight: Font.DemiBold
                }
                FormChoice {
                    id: alertFilter
                    objectName: "knowledgeAlertStatusFilter"
                    accessibleLabel: "筛选预警和处理记录状态"
                    model: [
                        { label: "全部状态", value: "" },
                        { label: "活动预警", value: "active" },
                        { label: "已延后", value: "snoozed" },
                        { label: "已处理", value: "handled" },
                        { label: "已解除", value: "resolved" }
                    ]
                    textRole: "label"
                    valueRole: "value"
                    onActivated: root.statusFilter = currentValue
                }
            }

            Text {
                visible: root.visibleAlerts.length === 0
                Layout.fillWidth: true
                text: root.alerts.length === 0 ? "当前学科没有预警或处理记录。" : "此状态下没有预警记录。"
                textFormat: Text.PlainText
                color: Theme.muted
                font.family: Theme.fontFamily
                font.pixelSize: 16
                wrapMode: Text.Wrap
            }

            Repeater {
                model: root.visibleAlerts
                delegate: PaperCard {
                    id: alertCard
                    required property var modelData
                    objectName: "knowledgeAlertCard_" + String(modelData.id)
                    Layout.fillWidth: true
                    paperColor: Theme.paper

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3
                            Text {
                                Layout.fillWidth: true
                                text: String(alertCard.modelData.path || "未知知识点")
                                textFormat: Text.PlainText
                                color: Theme.ink
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.bodyFontSize
                                font.weight: Font.DemiBold
                                wrapMode: Text.Wrap
                            }
                            Text {
                                Layout.fillWidth: true
                                text: root.typeLabel(alertCard.modelData.type) + " · " +
                                      root.display(alertCard.modelData.asOfDate, "")
                                textFormat: Text.PlainText
                                color: Theme.muted
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.captionFontSize
                                wrapMode: Text.Wrap
                            }
                        }
                        StatusPill {
                            text: root.statusLabel(alertCard.modelData.status)
                            tone: alertCard.modelData.status === "active" ? Theme.warning : Theme.leaf
                        }
                    }

                    BodyText {
                        text: "观察 " + root.display(alertCard.modelData.evidence.observationCount, "0") +
                              " · 练习 " + root.display(alertCard.modelData.evidence.exerciseCount, "0") +
                              " · 证据置信度 " + (alertCard.modelData.evidence.confidence === undefined ||
                              alertCard.modelData.evidence.confidence === null ? "未记录" :
                              Math.round(Number(alertCard.modelData.evidence.confidence) * 100) + "%") +
                              " · 建议日期 " + root.display(alertCard.modelData.recommendedDate, "未记录")
                    }
                    BodyText {
                        visible: alertCard.modelData.snoozedUntil !== null && alertCard.modelData.snoozedUntil !== undefined
                        color: Theme.accent
                        text: "已延后至 " + root.display(alertCard.modelData.snoozedUntil, "")
                    }
                    BodyText {
                        visible: alertCard.modelData.handledAt !== null && alertCard.modelData.handledAt !== undefined
                        color: Theme.leaf
                        text: "处理日期 " + root.display(alertCard.modelData.handledAt, "")
                    }

                    CheckBox {
                        id: evidenceDisclosure
                        objectName: "knowledgeAlertEvidenceToggle_" + String(alertCard.modelData.id)
                        text: "查看判定依据与近期错误"
                        Accessible.name: "展开预警 " + String(alertCard.modelData.id) + " 的学习证据与判定依据"
                    }
                    ColumnLayout {
                        visible: evidenceDisclosure.checked
                        Layout.fillWidth: true
                        spacing: 5
                        BodyText {
                            visible: alertCard.modelData.evidence.recentError !== null && alertCard.modelData.evidence.recentError !== undefined
                            text: {
                                const error = alertCard.modelData.evidence.recentError || ({})
                                return "最近错误：" + root.display(error.title, "记录中的失败") +
                                       " · " + root.display(error.occurred_on || error.date, "")
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: "判定依据"
                            textFormat: Text.PlainText
                            color: Theme.ink
                            font.family: Theme.fontFamily
                            font.pixelSize: 16
                            font.weight: Font.DemiBold
                        }
                        Repeater {
                            model: root.list(alertCard.modelData.explanation && alertCard.modelData.explanation.layers)
                                   .filter(function(layer) { return layer.name !== "workflow" })
                            delegate: ColumnLayout {
                                id: explanationLayer
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 3
                                Text {
                                    Layout.fillWidth: true
                                    text: "依据 · " + root.layerLabel(explanationLayer.modelData.name)
                                    textFormat: Text.PlainText
                                    color: Theme.ink
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.captionFontSize
                                    font.weight: Font.DemiBold
                                }
                                Repeater {
                                    model: root.businessFacts(explanationLayer.modelData)
                                    delegate: BodyText {
                                        required property var modelData
                                        color: Theme.muted
                                        font.pixelSize: Theme.captionFontSize
                                        text: root.factLabel(modelData) + "：" +
                                              root.factValue(modelData)
                                    }
                                }
                            }
                        }
                        BodyText {
                            visible: alertCard.modelData.explanation === null || alertCard.modelData.explanation === undefined
                            color: Theme.muted
                            text: "当前分类版本与预警快照不一致；保留已保存证据和事件，不生成新的分类解释。"
                        }
                    }

                    CheckBox {
                        id: alertAuditToggle
                        objectName: "knowledgeAlertAuditToggle_" + String(alertCard.modelData.id)
                        text: "展开规则版本与来源（审计）"
                        Accessible.name: text
                    }
                    ColumnLayout {
                        visible: alertAuditToggle.checked
                        Layout.fillWidth: true
                        spacing: 4
                        Text {
                            Layout.fillWidth: true
                            text: "实例 " + String(alertCard.modelData.id) + " · 证据版本 " +
                                  root.display(alertCard.modelData.evidenceVersion, "未知") +
                                  " · 规则版本 " + root.display(alertCard.modelData.ruleVersion, "未知")
                            textFormat: Text.PlainText
                            color: Theme.muted
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.captionFontSize
                            wrapMode: Text.Wrap
                        }
                        Repeater {
                            model: root.list(alertCard.modelData.explanation && alertCard.modelData.explanation.layers)
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
                                        text: root.factLabel(modelData) + "：" + root.factValue(modelData) +
                                              " · 来源/版本 " + root.display(modelData.sourceReference, "未知")
                                    }
                                }
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "事件与审计记录"
                            textFormat: Text.PlainText
                            color: Theme.ink
                            font.family: Theme.fontFamily
                            font.pixelSize: 16
                            font.weight: Font.DemiBold
                        }
                        Repeater {
                            model: root.list(alertCard.modelData.events)
                            delegate: BodyText {
                                required property var modelData
                                color: Theme.muted
                                font.pixelSize: Theme.captionFontSize
                                text: root.eventText(modelData)
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Item { Layout.fillWidth: true }
                        ActionButton {
                            objectName: "knowledgeAlertSnooze_" + String(alertCard.modelData.id)
                            text: "延后"
                            accessibleLabel: "延后预警 " + String(alertCard.modelData.id)
                            visible: alertCard.modelData.status === "active" || alertCard.modelData.status === "snoozed"
                            enabled: visible && root.work && !root.work.busy
                            onClicked: root.beginAction(alertCard.modelData, "snooze")
                        }
                        ActionButton {
                            objectName: "knowledgeAlertHandle_" + String(alertCard.modelData.id)
                            text: "标记已处理"
                            primary: true
                            accessibleLabel: "标记预警 " + String(alertCard.modelData.id) + " 为已处理"
                            visible: alertCard.modelData.status === "active"
                            enabled: visible && root.work && !root.work.busy
                            onClicked: root.beginAction(alertCard.modelData, "handle")
                        }
                    }
                }
            }
        }
    }

    Dialog {
        id: actionDialog
        objectName: "knowledgeAlertConfirmDialog"
        modal: true
        title: root.pendingAction === "handle" ? "确认处理预警" : "确认延后预警"
        width: Math.min(460, Math.max(300, root.width - 36))
        anchors.centerIn: Overlay.overlay
        standardButtons: Dialog.NoButton
        closePolicy: Popup.CloseOnEscape
        background: Rectangle {
            radius: 14
            color: Theme.card
            border.width: 1
            border.color: Theme.line
        }
        contentItem: ColumnLayout {
            spacing: 12
            Text {
                Layout.fillWidth: true
                text: root.pendingAlert ? String(root.pendingAlert.path || "未知知识点") : ""
                textFormat: Text.PlainText
                color: Theme.ink
                font.family: Theme.fontFamily
                font.pixelSize: Theme.bodyFontSize
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
            }
            BodyText {
                visible: root.pendingAction === "handle"
                text: "将此预警标记为已处理？"
            }
            BodyText {
                visible: root.pendingAction === "snooze"
                text: "请选择延后到期日。保存会保留这条预警，并把本次延后写入事件记录。日期不得早于 " +
                      root.display(root.pendingAlert ? root.pendingAlert.asOfDate : root.asOfDate, "计算日期") + "。"
            }
            FormField {
                id: snoozeDateField
                objectName: "knowledgeAlertSnoozeDateField"
                visible: root.pendingAction === "snooze"
                placeholderText: "YYYY-MM-DD"
                accessibleLabel: "预警延后到期日期，格式 YYYY-MM-DD"
                text: root.snoozeDate
                onTextChanged: root.snoozeDate = text
            }
            Text {
                Layout.fillWidth: true
                visible: root.pendingAction === "snooze" && snoozeDateField.text.length > 0 && !root.validSnoozeDate()
                text: "请填写真实日期，且不得早于计算日期。"
                textFormat: Text.PlainText
                color: Theme.warning
                font.family: Theme.fontFamily
                font.pixelSize: Theme.captionFontSize
                wrapMode: Text.Wrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                ActionButton {
                    objectName: "knowledgeAlertCancelConfirmButton"
                    text: "返回"
                    accessibleLabel: "取消预警操作"
                    onClicked: actionDialog.close()
                }
                ActionButton {
                    objectName: "knowledgeAlertActionConfirmButton"
                    text: root.pendingAction === "handle" ? "确认处理" : "确认延后"
                    primary: true
                    accessibleLabel: text + "并保存事件记录"
                    enabled: root.work && !root.work.busy &&
                             (root.pendingAction !== "snooze" || root.validSnoozeDate())
                    onClicked: root.submitConfirmedAction()
                }
            }
        }
    }

    Connections {
        target: root.work
        enabled: root.work !== null && root.work !== undefined
        ignoreUnknownSignals: true
        function onDataChanged() {
            if (actionDialog.visible) {
                actionDialog.close()
                root.pendingAlert = null
                root.pendingAction = ""
                root.snoozeDate = ""
            }
        }
    }
}
