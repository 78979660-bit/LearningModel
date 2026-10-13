import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "../components"

ScrollView {
    id: root
    objectName: "planningPane"
    required property var workspace
    property bool surfacesReady: true
    // Retained panes reconcile on entry and read completion as well as DTO
    // changes. A read can return the same snapshot without another notify.
    property var planData: ({})
    readonly property bool initialDataReady: !workspace || Object.keys(planData).length > 0 || !!workspace.error
    SnapshotView {
        id: planSnapshot
        objectName: "planSnapshot"
        source: root.workspace
        dataProperty: "planData"
        pageVisible: root.visible
    }
    readonly property var subjects: (planData.catalog || {}).subjects || []
    readonly property var budgetDraft: workspace ? workspace.budgetPreview : ({})
    readonly property var planDraft: workspace ? workspace.planPreview : ({})
    readonly property string selectedSubject: scope.currentIndex > 0 ? scope.currentText : ""
    property var estimates: ({})
    property var examDates: ({})
    property bool estimatesExpanded: false
    property bool detailExpanded: false
    property bool estimatesCreated: false
    property bool detailCreated: false
    onEstimatesExpandedChanged: if (estimatesExpanded) estimatesCreated = true
    onDetailExpandedChanged: if (detailExpanded) detailCreated = true
    property bool ready: false
    onVisibleChanged: {
        if (visible && ready) syncPlanSnapshot()
        if (!visible && ready) {
            if (phaseDialogLoader.item) phaseDialogLoader.item.close()
            if (archiveDialogLoader.item) archiveDialogLoader.item.close()
        }
    }
    contentWidth: availableWidth
    contentHeight: body.implicitHeight + 20
    clip: true

    function invalidateBudget() {
        if (ready && workspace) workspace.invalidatePreview("budget")
    }
    function invalidatePlan() {
        if (ready && workspace) workspace.invalidatePreview("plan")
    }
    function syncPlanSnapshot() {
        planSnapshot.sync()
        const snapshot = planSnapshot.value
        // Own the outer JS object instead of retaining a QVariantMap wrapper.
        // Leave bindings and user edits alone when this pane already owns the
        // current snapshot; only reconcile a missing or outdated view.
        if (JSON.stringify(planData) !== JSON.stringify(snapshot || ({}))) {
            const minutesDraft = budgetMinutes.text
            planData = snapshot
            if (ready) budgetMinutes.text = minutesDraft
        }
        syncScope()
    }
    function syncScope() {
        const selected = String(((root.planData.catalog || {}).selectedScope || {}).subject || "")
        scope.currentIndex = Math.max(0, scope.model.indexOf(selected))
    }
    function budgetPayload() {
        return {
            scope: selectedSubject || null,
            budget_minutes: Number(budgetMinutes.text),
            exam_dates: examDates,
            estimate_minutes: estimates
        }
    }
    function rows(plan) { return plan && plan.items ? plan.items : [] }
    function line(item) { return String(item.itemText || item.item_text || item.title || "") }
    function taskRows() {
        if (budgetDraft && budgetDraft.token)
            return budgetDraft.rows || []
        if (planData.budgetPlan && rows(planData.budgetPlan).length)
            return rows(planData.budgetPlan)
        return planData.tasks || []
    }
    function sectionLabel(item) {
        const labels = {selected: "安排", completed: "已完成", excluded: "排除"}
        if (item.section) return labels[String(item.section)] || String(item.section)
        if (item.checked) return "已完成"
        if (item.excludedReason) return "已排除"
        if (!budgetDraft.token && rows(planData.budgetPlan).length > 0) return "已安排"
        if (item.estimatedMinutes === null || item.estimatedMinutes === undefined) return "待估时"
        return "待安排"
    }
    function scopeSummary() {
        if (!selectedSubject)
            return subjects.length ? subjects.length + " 个活动学科" : (Object.keys(planData).length ? "暂无活动学科" : "")
        const selected = subjects.find(s => s.name === selectedSubject)
        return selected ? String(selected.phaseLabel || selected.phase || "") : ""
    }

    Component.onCompleted: {
        syncPlanSnapshot()
        if (workspace) {
            workspace.invalidatePreview("budget")
            workspace.invalidatePreview("plan")
        }
        ready = true
    }
    Connections {
        target: root.workspace
        function onPlanDataChanged() {
            root.syncPlanSnapshot()
            budgetMinutes.text = root.planData.budget ? String(root.planData.budget.availableMinutes) : ""
            root.estimates = ({})
            root.examDates = root.planData.examDates || ({})
        }
        function onStateChanged() {
            if (root.ready && root.workspace && !root.workspace.busy)
                root.syncPlanSnapshot()
        }
    }

    ColumnLayout {
        id: body
        width: root.availableWidth
        spacing: 12

        PaperCard {
            surfaceEnabled: root.surfacesReady
            objectName: "planScopeCard"
            Layout.fillWidth: true
            padding: 8
            paperColor: Theme.paper
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 190
                    Layout.preferredWidth: 240
                    spacing: 3
                    BodyText { text: "学科范围"; font.weight: Font.DemiBold }
                    FormChoice {
                        id: scope
                        objectName: "planScope"
                        accessibleLabel: "学习计划学科范围"
                        model: ["全部活动学科"].concat(root.subjects.map(s => s.name))
                        enabled: root.workspace && !root.workspace.busy
                        onActivated: {
                            root.estimates = ({})
                            root.invalidateBudget()
                            root.invalidatePlan()
                            root.workspace.run("load", {scope: root.selectedSubject || null})
                        }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: root.availableWidth >= 700
                    spacing: 3
                    BodyText {
                        text: root.scopeSummary()
                        color: Theme.muted
                        maximumLineCount: 1
                        elide: Text.ElideRight
                    }
                }
                ActionButton {
                    objectName: "editStudyPhase"
                    text: "学习阶段"
                    accessibleLabel: "调整所选学科学习阶段"
                    enabled: root.workspace && !root.workspace.busy && root.selectedSubject.length > 0
                    onClicked: {
                        const subject = root.subjects.find(s => s.name === root.selectedSubject)
                        phaseDialogLoader.active = true
                        phaseDialogLoader.item.finalReviewChecked = !!subject && subject.phase === "final_review"
                        phaseDialogLoader.item.open()
                    }
                }
            }
        }

        GridLayout {
            id: mainColumns
            objectName: "planningMainColumns"
            Layout.fillWidth: true
            columns: width >= 780 ? 2 : 1
            columnSpacing: 14
            rowSpacing: 12

            PaperCard {
                surfaceEnabled: root.surfacesReady
                objectName: "budgetTaskListCard"
                Layout.fillWidth: true
                Layout.preferredWidth: mainColumns.width * 0.66
                Layout.minimumWidth: 0
                Layout.alignment: Qt.AlignTop
                padding: 22
                paperColor: Theme.paper
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        Layout.fillWidth: true
                        text: "今日学习任务"
                        textFormat: Text.PlainText
                        color: Theme.ink
                        font.family: Theme.headingFontFamily
                        font.pixelSize: Theme.sectionFontSize
                        font.weight: Font.DemiBold
                    }
                    StatusPill {
                        text: root.budgetDraft.token ? "预算预览" : (root.planData.budgetPlan ? "已确认" : "待安排")
                        tone: root.budgetDraft.token ? Theme.accent : Theme.leaf
                    }
                }
                BodyText {
                    text: root.budgetDraft.token
                          ? "预算预览待复核"
                          : root.planData.budgetPlan
                              ? "已确认任务状态可更新"
                              : "填写可用时间与估时后预览安排"
                    color: Theme.muted
                    font.pixelSize: Theme.captionFontSize
                }
                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.max(260, Math.min(420, root.height - 245))
                    ListView {
                        id: taskList
                        objectName: "budgetTaskList"
                        anchors.fill: parent
                        clip: true
                        cacheBuffer: 100
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                        model: root.taskRows()
                        delegate: ColumnLayout {
                            required property var modelData
                            required property int index
                            objectName: "budgetTaskRow_" + index
                            width: taskList.width - 12
                            height: Math.max(88, implicitHeight)
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 10
                                CheckBox {
                                    objectName: "budget-check-" + modelData.id
                                    visible: !!root.planData.budgetPlan && modelData.id !== undefined && !modelData.section
                                    checked: !!modelData.checked
                                    Accessible.name: "预算任务完成状态：" + root.line(modelData)
                                    enabled: root.workspace && !root.workspace.busy && !modelData.excludedReason
                                    onClicked: root.workspace.run("plan.check", {id: modelData.id, checked: checked})
                                }
                                Rectangle {
                                    Layout.preferredWidth: 5
                                    Layout.preferredHeight: 30
                                    radius: 2
                                    color: modelData.checked ? Theme.leaf : Theme.accent
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 2
                                    BodyText {
                                        text: root.line(modelData) || ("学习任务 " + (index + 1))
                                        font.weight: Font.DemiBold
                                    }
                                    BodyText {
                                        visible: String(modelData.reason || modelData.excludedReason || "").length > 0
                                        text: String(modelData.reason || modelData.excludedReason || "")
                                        color: Theme.muted
                                        font.pixelSize: Theme.captionFontSize
                                    }
                                }
                                ColumnLayout {
                                    spacing: 2
                                    BodyText {
                                        objectName: "budgetTaskStatus_" + index
                                        text: root.sectionLabel(modelData)
                                        color: modelData.section === "excluded" || modelData.excludedReason ? Theme.warning : Theme.accent
                                        font.weight: Font.DemiBold
                                    }
                                    BodyText {
                                        text: modelData.estimatedMinutes === null || modelData.estimatedMinutes === undefined
                                              ? "时长未知" : String(modelData.estimatedMinutes) + " 分钟"
                                        color: Theme.muted
                                        font.pixelSize: Theme.captionFontSize
                                    }
                                }
                            }
                            Rectangle {
                                visible: index < root.taskRows().length - 1
                                Layout.fillWidth: true
                                Layout.preferredHeight: 1
                                Layout.topMargin: 4
                                color: Theme.line
                            }
                        }
                    }
                    BodyText {
                        anchors.centerIn: parent
                        width: parent.width
                        visible: taskList.count === 0
                        text: !Object.keys(root.planData).length ? "正在读取学习任务…"
                              : root.subjects.length ? "当前范围没有待安排任务。" : "暂无活动学科"
                        color: Theme.muted
                    }
                }
            }

            PaperCard {
                surfaceEnabled: root.surfacesReady
                objectName: "budgetPlanCard"
                Layout.fillWidth: true
                Layout.preferredWidth: mainColumns.width * 0.34
                Layout.minimumWidth: 0
                Layout.alignment: Qt.AlignTop
                padding: 16
                paperColor: Theme.peach
                BodyText { text: "时间预算"; font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
                BodyText { text: "今日可用时间（0–1440 分钟）"; color: Theme.muted; font.pixelSize: Theme.captionFontSize }
                BodyText {
                    visible: false; text: ""
                    color: Theme.muted
                    font.pixelSize: Theme.captionFontSize
                }
                FormField {
                    id: budgetMinutes
                    objectName: "budgetMinutes"
                    accessibleLabel: "今日可用时间，分钟"
                    placeholderText: "填写分钟数"
                    validator: IntValidator { bottom: 0; top: 1440 }
                    text: root.planData.budget ? String(root.planData.budget.availableMinutes) : ""
                    enabled: root.workspace && !root.workspace.busy
                    onTextEdited: root.invalidateBudget()
                }
                BodyText {
                    visible: !!root.budgetDraft.token
                    text: "已安排 " + String((root.budgetDraft.summary || {}).plannedMinutes || 0)
                          + " 分钟 · 余量 " + String((root.budgetDraft.summary || {}).remainingMinutes || 0) + " 分钟"
                    color: Theme.ink
                    font.weight: Font.DemiBold
                }
                BodyText {
                    visible: !!root.budgetDraft.token
                             && (!!(root.budgetDraft.summary || {}).overBudgetCompletedMinutes
                                 || !!(root.budgetDraft.summary || {}).completedOccupancyUnknown)
                    text: "已完成任务超预算 " + String((root.budgetDraft.summary || {}).overBudgetCompletedMinutes || 0)
                          + " 分钟" + ((root.budgetDraft.summary || {}).completedOccupancyUnknown ? " · 部分已完成任务时长未知" : "")
                    color: Theme.warning
                    font.pixelSize: Theme.captionFontSize
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: 8
                    ActionButton {
                        objectName: "previewBudget"
                        text: "预览安排"
                        primary: true
                        enabled: root.workspace && !root.workspace.busy
                                 && budgetMinutes.acceptableInput && budgetMinutes.text.length > 0
                        onClicked: root.workspace.run("budget.preview", root.budgetPayload())
                    }
                    ActionButton {
                        objectName: "confirmBudget"
                        text: "确认预算"
                        enabled: root.workspace && !root.workspace.busy
                                 && !!root.budgetDraft.token && budgetReviewed.checked
                        onClicked: root.workspace.run("budget.confirm", {token: root.budgetDraft.token})
                    }
                }
                CheckBox {
                    id: budgetReviewed
                    objectName: "budgetReviewed"
                    Layout.fillWidth: true
                    text: "我已复核预算、估时与选择原因"
                    Accessible.name: text
                    enabled: !!root.budgetDraft.token && root.workspace && !root.workspace.busy
                }
                Connections {
                    target: root.workspace
                    function onPreviewsChanged() { budgetReviewed.checked = false }
                }
                BodyText {
                    visible: !!root.planData.budgetPlan
                    text: "预算计划已保存"
                    color: Theme.leaf
                    font.weight: Font.DemiBold
                }
                Repeater {
                    model: root.planData.unmapped || []
                    delegate: BodyText {
                        required property var modelData
                        text: "未映射：" + modelData.title + " · " + modelData.reason
                        color: Theme.warning
                        font.pixelSize: Theme.captionFontSize
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            ActionButton {
                objectName: "planEstimatesToggle"
                text: root.estimatesExpanded ? "收起考试日期与任务估时" : "考试日期与任务估时"
                accessibleLabel: text
                onClicked: root.estimatesExpanded = !root.estimatesExpanded
            }
            BodyText {
                Layout.fillWidth: true
                text: root.subjects.length + " 个学科 · " + (root.planData.tasks || []).length + " 项任务"
                color: Theme.muted
                font.pixelSize: Theme.captionFontSize
            }
        }
        Loader {
            Layout.fillWidth: true
            active: root.estimatesCreated
            visible: root.estimatesExpanded
            asynchronous: true
            onActiveChanged: if (active) setSource(Qt.resolvedUrl("PlanningEstimates.qml"), {plan: root})
        }

        PaperCard {
            surfaceEnabled: root.surfacesReady
            objectName: "detailedPlanCard"
            Layout.fillWidth: true
            Layout.maximumWidth: root.detailExpanded ? 1200 : 480
            padding: 12
            paperColor: Theme.paper
            RowLayout {
                Layout.fillWidth: true
                BodyText {
                    Layout.fillWidth: true
                    text: "详细学习计划与反馈"
                    font.pixelSize: Theme.sectionFontSize
                    font.weight: Font.DemiBold
                }
                ActionButton {
                    objectName: "detailedPlanToggle"
                    text: root.detailExpanded ? "收起" : "展开"
                    accessibleLabel: root.detailExpanded ? "收起详细学习计划" : "展开详细学习计划"
                    onClicked: root.detailExpanded = !root.detailExpanded
                }
            }
            BodyText {
                visible: !!root.planData.currentPlan
                text: root.planData.currentPlan
                      ? "已保存计划：" + String(root.planData.currentPlan.startDate || "") + " 至 " + String(root.planData.currentPlan.endDate || "")
                      : "可生成预览，并在复核后保存。"
                color: Theme.muted
                font.pixelSize: Theme.captionFontSize
            }
        }
        Loader {
            Layout.fillWidth: true
            active: root.detailCreated
            visible: root.detailExpanded
            asynchronous: true
            onActiveChanged: if (active) setSource(Qt.resolvedUrl("DetailedPlanPane.qml"), {plan: root})
            onLoaded: item.archiveRequested.connect(function() { archiveDialogLoader.active = true; archiveDialogLoader.item.open() })
        }
        Item { implicitHeight: 8 }
    }

    Loader {
        id: phaseDialogLoader
        active: false
        sourceComponent: Dialog {
            id: phaseDialog
            property alias finalReviewChecked: finalReview.checked
            parent: Overlay.overlay
            anchors.centerIn: parent
            width: Math.min(460, root.width - 24)
            title: "确认学习阶段调整"
            modal: true
            standardButtons: Dialog.Ok | Dialog.Cancel
            contentItem: ColumnLayout {
                BodyText { text: root.selectedSubject + "：阶段改变后原活动计划会归档，学习记录保留。" }
                CheckBox { id: finalReview; text: "进入期末复习阶段"; Accessible.name: text }
            }
            onAccepted: {
                const states = ({})
                states[root.selectedSubject] = finalReview.checked
                root.workspace.run("plan.phase", {states: states})
            }
        }
    }
    Loader {
        id: archiveDialogLoader
        active: false
        sourceComponent: Dialog {
            id: archiveDialog
            implicitHeight: 220
            parent: Overlay.overlay
            anchors.centerIn: parent
            width: Math.min(460, root.width - 24)
            title: "确认归档当前计划"
            modal: true
            standardButtons: Dialog.Ok | Dialog.Cancel
            contentItem: BodyText { text: "归档后该计划不再作为活动计划，已存在的学习记录保留。" }
            onAccepted: root.workspace.run("plan.archive", {scope: root.selectedSubject || null})
        }
    }
}
