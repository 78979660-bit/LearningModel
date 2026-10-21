import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import ".."
import "../components"

Item {
    id: root
    objectName: "assistantPage"
    required property var bridge
    readonly property var work: bridge ? bridge.insights : null
    readonly property var assistantState: assistantSnapshot.value
    SnapshotView {
        id: assistantSnapshot
        objectName: "assistantSnapshot"
        source: root.work
        dataProperty: "assistantData"
        pageVisible: root.visible
    }
    readonly property var llmState: (assistantState.privacy || {}).llm || ({})
    readonly property string modelStatus: !external.checked ? "本次使用本地回答"
        : Object.keys(llmState).length === 0 ? "正在检查外部模型设置…"
        : llmState.enabled !== true ? "外部模型未开启，将使用本地回答"
        : llmState.natural_query_enabled !== true ? "外部问答未开启，将使用本地回答"
        : llmState.credential_configured !== true ? "外部模型尚未配置密钥，将使用本地回答"
        : "外部模型：" + (llmState.model || "已配置模型")
    readonly property bool initialDataReady: !work || Object.keys(assistantState).length > 0 || !!work.error
    readonly property bool preloadReady: initialDataReady
    readonly property var preview: work ? work.proposalPreview : ({})
    readonly property var undo: work ? work.undoPreview : ({})
    property var attachments: []
    property string pendingPolicy: ""
    property bool pendingPolicyValue: false
    property string pendingPolicyLabel: ""
    readonly property var messages: work && work.conversation && work.conversation.length > 0
        ? work.conversation : (work && Object.keys(work.answer || {}).length > 0
                              ? [{id: "previous-answer", role: "assistant", response: work.answer}] : [])
    property bool followLatest: true

    function messageText(message) {
        if (message.role === "user" || message.pending || message.failed) return message.text || ""
        const answer = (message.response || {}).answer || {}
        return [readable(answer.answer), readable(answer.actions), readable(answer.clarifications),
                readable(answer.warnings)].filter(Boolean).join("\n\n")
    }
    function scrollToLatest() {
        if (followLatest) scrollToEnd.restart()
    }
    function spacedReply(text) {
        const paragraphs = text.split(/\r?\n+/)
        return paragraphs.map((paragraph, index) => {
            const escaped = paragraph.replace(/&/g, "&amp;").replace(/</g, "&lt;")
                .replace(/>/g, "&gt;").replace(/\"/g, "&quot;")
            const gap = index === paragraphs.length - 1 ? 0 : 12
            return '<p style="line-height:150%; margin-top:0px; margin-bottom:' + gap
                + 'px; white-space:pre-wrap;">' + escaped + '</p>'
        }).join("")
    }
    function askQuestion(text) {
        if (!work || work.busy || question.inputMethodComposing) return
        const value = typeof text === "string" ? text : question.text
        if (!value.trim()) return
        const request = payload()
        request.text = value
        work.run("assistant.ask", request)
        if (work.busy) {
            question.clear()
            followLatest = true
            scrollToLatest()
        }
    }
    function handleSendKey(event) {
        event.accepted = false
        if (question.inputMethodComposing) { event.accepted = true; return }
        if (event.modifiers & Qt.ShiftModifier) return
        event.accepted = true
        if (!event.isAutoRepeat) askQuestion()
    }

    function readable(value) {
        if (typeof value === "string") return value
        if (value === undefined || value === null || typeof value.length !== "number") return ""
        const lines = []
        for (let index = 0; index < value.length; ++index) lines.push(String(value[index]))
        return lines.join("\n")
    }
    function percent(value) { return value === undefined || value === null ? "暂无估计" : Math.round(Number(value) * 100) + "%" }
    function masteryLines(rows) {
        return (rows || []).map(item => [item.subject, item.module, item.topic].filter(Boolean).join(" / ")
            + "：" + percent(item.mastery_before) + " → " + percent(item.mastery_after)).join("\n")
    }
    function impactText() {
        const impact = preview.impact || {}, mastery = impact.mastery || {}, plan = impact.plan || {}
        return [plan.note || "", masteryLines(mastery.matched_topics), (mastery.notes || []).join("\n")].filter(Boolean).join("\n") || "暂无可靠的掌握度变化估计。"
    }
    function disclosureText() {
        const disclosure = preview.disclosure || {}
        return disclosure.will_contact_external ? "将联系外部顾问：" + (disclosure.provider || "已配置服务")
            + "\n发送内容：" + (disclosure.fields_sent || []).join("、") : "本次提案未披露需要外发的内容。"
    }
    function receiptText() {
        const receipt = work ? work.receipt : {}
        return [(receipt.message || (receipt.saved ? "操作已保存。" : "业务未提交。")),
            receipt.recordId ? "学习记录编号：" + receipt.recordId : "", receipt.alertId ? "预警编号：" + receipt.alertId : "",
            masteryLines(receipt.masteryChanges), (receipt.warnings || []).join("\n"), receipt.refreshWarningText || ""].filter(Boolean).join("\n")
    }
    function invalidate() { if (work) work.invalidatePreview("proposal"); reviewed.checked = false }
    function payload() { return {text: question.text, attachments: attachments, external: external.checked} }

    property bool pageReady: false
    property bool entered: false
    function enterPage() {
        if (!visible || entered || !work) return
        entered = true
        work.loadAssistantForPage()
    }
    Component.onCompleted: { pageReady = true; enterPage() }
    onVisibleChanged: {
        if (!pageReady) return
        if (visible) enterPage()
        else {
            entered = false
            moreActionsDialog.close(); policyDialog.close(); files.close()
        }
    }
    Connections {
        target: root.work
        function onPreviewsChanged() { reviewed.checked = false; undoReviewed.checked = false }
        function onDataChanged() { root.scrollToLatest() }
    }
    Timer {
        id: scrollToEnd
        interval: 16
        onTriggered: {
            const view = scroll.contentItem
            if (view && root.followLatest) view.contentY = Math.max(0, view.contentHeight - view.height)
        }
    }

    ColumnLayout {
        width: Math.min(Math.max(0, root.width - (root.width < 820 ? 32 : Theme.pageMargin * 2)), 1000)
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.topMargin: 16
        anchors.bottomMargin: 16
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: root.height < 650 ? 68 : 86
            spacing: 8
            BodyText {
                Layout.fillWidth: false
                text: "学习助理"
                font.family: Theme.headingFontFamily
                font.pixelSize: root.width >= 900 ? Theme.pageTitleSize : 36
                font.weight: Font.DemiBold
            }
            BusyIndicator { running: root.work ? root.work.busy : false; opacity: running ? 1 : 0; implicitWidth: 24; implicitHeight: 24 }
            ActionButton {
                objectName: "assistantRefresh"
                text: ""
                iconSource: Qt.resolvedUrl("../assets/icons/refresh.svg")
                accessibleLabel: "刷新"
                padding: 8
                Layout.minimumWidth: 46
                Layout.preferredWidth: 46
                Layout.maximumWidth: 46
                enabled: root.work && !root.work.busy
                onClicked: root.work.run("assistant.load", {})
            }
            Item { Layout.fillWidth: true }
            ActionButton {
                objectName: "assistantMoreActions"
                text: "更多操作"
                Layout.preferredWidth: 124
                onClicked: moreActionsDialog.open()
            }
        }

        BodyText {
            objectName: "insightsStatus"
            visible: root.work && !!root.work.error
            text: root.work ? root.work.error : ""
            color: Theme.warning
            font.pixelSize: Theme.captionFontSize
            Accessible.role: Accessible.StaticText
        }

        PaperCard {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 100
            padding: 12
            paperColor: Theme.paper

            ScrollView {
                id: scroll
                objectName: "assistantScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: availableWidth
                contentHeight: resultColumn.implicitHeight + 12
                onContentHeightChanged: root.scrollToLatest()
                clip: true
                ScrollBar.vertical.policy: ScrollBar.AsNeeded

                Connections {
                    target: scroll.contentItem
                    function onMovementEnded() {
                        const view = scroll.contentItem
                        root.followLatest = view.contentY + view.height >= view.contentHeight - 40
                    }
                }

                ColumnLayout {
                    id: resultColumn
                    width: scroll.availableWidth
                    spacing: 18

                    ColumnLayout {
                        objectName: "assistantEmptyState"
                        Layout.fillWidth: true
                        visible: root.messages.length === 0 && !root.preview.token && Object.keys(root.work ? root.work.receipt : {}).length === 0
                        spacing: 10
                        Item { Layout.preferredHeight: 20 }
                        BodyText { text: "今天想从哪里开始？"; font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
                        BodyText { text: "可以聊聊不太熟的知识点，也可以一起看看今天先复习什么。"; color: Theme.muted }
                        Flow {
                            Layout.fillWidth: true
                            spacing: 8
                            Repeater {
                                model: ["今天先复习什么？", "哪些地方需要加强？"]
                                ActionButton {
                                    required property string modelData
                                    text: modelData
                                    enabled: root.work && !root.work.busy
                                    onClicked: root.askQuestion(modelData)
                                }
                            }
                        }
                    }

                    Repeater {
                        model: root.messages
                        Item {
                            id: chatRow
                            required property var modelData
                            required property int index
                            readonly property bool fromUser: modelData.role === "user"
                            readonly property bool latest: index === root.messages.length - 1
                            readonly property var response: modelData.response || ({})
                            readonly property var answer: response.answer || ({})
                            objectName: "assistantMessage-" + modelData.id
                            Layout.fillWidth: true
                            implicitHeight: speaker.height + 6 + bubble.height

                            Row {
                                id: speaker
                                x: chatRow.fromUser ? parent.width - width : 0
                                spacing: 6
                                height: 22
                                Rectangle {
                                    width: 22; height: 22; radius: 11
                                    color: chatRow.fromUser ? Theme.accentSoft : Theme.lavender
                                    Text {
                                        anchors.centerIn: parent
                                        text: chatRow.fromUser ? "你" : "助"
                                        color: Theme.accent
                                        font.family: Theme.fontFamily
                                        font.pixelSize: 12
                                    }
                                }
                                Text {
                                    text: chatRow.fromUser ? "你" : "学习助理"
                                    color: Theme.muted
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.captionFontSize
                                }
                            }

                            TextMetrics { id: userMetrics; font.family: Theme.fontFamily; font.pixelSize: Theme.bodyFontSize; text: chatRow.modelData.text || "" }
                            Rectangle {
                                id: bubble
                                x: chatRow.fromUser ? parent.width - width : 0
                                y: speaker.height + 6
                                width: chatRow.fromUser ? Math.min(parent.width * 0.86, Math.max(100, userMetrics.advanceWidth + 28)) : Math.min(parent.width * 0.9, 760)
                                height: bubbleContent.implicitHeight + 24
                                radius: 14
                                color: chatRow.fromUser ? Theme.accentSoft : Qt.alpha(Theme.card, 0.94)
                                border.width: chatRow.fromUser ? 0 : 1
                                border.color: Qt.alpha(Theme.line, 0.7)

                                Image {
                                    anchors.fill: parent
                                    anchors.margins: 2
                                    source: Qt.resolvedUrl("../assets/paper-texture.svg")
                                    sourceSize: Qt.size(960, 960)
                                    fillMode: Image.Tile
                                    opacity: chatRow.fromUser ? 0.25 : 0.48
                                    smooth: true
                                    asynchronous: false
                                }

                                ColumnLayout {
                                    id: bubbleContent
                                    x: 12; y: 12
                                    width: bubble.width - 24
                                    spacing: 8
                                    TextEdit {
                                        readonly property string plainText: root.messageText(chatRow.modelData)
                                        objectName: chatRow.latest && !chatRow.fromUser ? "assistantAnswer-answer" : "assistantText-" + chatRow.modelData.id
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: Math.max(24, contentHeight)
                                        text: chatRow.fromUser ? plainText : root.spacedReply(plainText)
                                        textFormat: chatRow.fromUser ? TextEdit.PlainText : TextEdit.RichText
                                        readOnly: true
                                        selectByMouse: true
                                        wrapMode: TextEdit.Wrap
                                        color: Theme.ink
                                        selectionColor: Theme.accentSoft
                                        selectedTextColor: Theme.ink
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.bodyFontSize
                                        Accessible.role: Accessible.StaticText
                                        Accessible.name: plainText
                                    }
                                    BusyIndicator { visible: chatRow.modelData.pending === true; running: visible; implicitWidth: 22; implicitHeight: 22 }
                                    BodyText {
                                        visible: !chatRow.fromUser && chatRow.response.external_requested === true && chatRow.response.external_used !== true
                                        text: chatRow.response.external_error || "外部顾问暂时不可用，这次先参考本机的学习记录。"
                                        color: Theme.muted
                                        font.pixelSize: Theme.captionFontSize
                                    }
                                    ActionButton {
                                        visible: chatRow.modelData.failed === true
                                        text: "重新发送"
                                        enabled: root.work && !root.work.busy
                                        onClicked: root.askQuestion(chatRow.modelData.question)
                                    }
                                    ToolButton {
                                        id: answerDetails
                                        objectName: chatRow.latest ? "assistantAnswerDetails" : "assistantDetails-" + chatRow.modelData.id
                                        visible: !chatRow.fromUser && !chatRow.modelData.pending && (root.readable(chatRow.answer.evidence).length > 0 || root.readable(chatRow.answer.source).length > 0)
                                        checkable: true
                                        text: checked ? "收起参考记录 ▴" : "为什么这样建议 ▾"
                                        Accessible.name: text
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.captionFontSize
                                        onToggled: root.followLatest = false
                                    }
                                    ColumnLayout {
                                        visible: answerDetails.visible && answerDetails.checked
                                        Layout.fillWidth: true
                                        spacing: 6
                                        BodyText {
                                            objectName: chatRow.latest ? "assistantAnswer-evidence" : "assistantEvidence-" + chatRow.modelData.id
                                            text: root.readable(chatRow.answer.evidence)
                                            color: Theme.muted
                                            font.pixelSize: Theme.captionFontSize
                                        }
                                        BodyText {
                                            objectName: chatRow.latest ? "assistantAnswer-source" : "assistantSource-" + chatRow.modelData.id
                                            text: root.readable(chatRow.answer.source)
                                            color: Theme.muted
                                            font.pixelSize: Theme.captionFontSize
                                        }
                                    }
                                }
                            }
                        }
                    }

                    PaperCard {
                        Layout.fillWidth: true
                        visible: !!root.preview.token
                        paperColor: Theme.peach
                        BodyText { text: "先看看这份安排"; font.pixelSize: 20; font.weight: Font.DemiBold }
                        BodyText { objectName: "assistantPreviewSummary"; text: root.readable(root.preview.summary) }
                        BodyText { text: "预计影响\n" + root.impactText() }
                        BodyText { text: "来源与披露\n" + root.disclosureText(); color: Theme.muted }
                        BodyText { text: (root.preview.warnings || []).join("\n"); color: Theme.warning; visible: text.length > 0 }
                        CheckBox {
                            id: reviewed
                            objectName: "assistantReviewed"
                            text: "我已完整复核目标、变更与外发内容"
                            Accessible.name: text
                            enabled: root.work && !root.work.busy
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: 7
                            ActionButton {
                                objectName: "assistantConfirm"
                                text: "确认执行"
                                primary: true
                                enabled: root.work && !root.work.busy && reviewed.checked && root.preview.confirmationAllowed !== false
                                onClicked: root.work.run("assistant.confirm", {token: root.preview.token, confirmed: true})
                            }
                            ActionButton {
                                objectName: "assistantAuto"
                                text: "按已启用策略执行"
                                visible: root.preview.autoEnabled === true
                                enabled: root.work && !root.work.busy && reviewed.checked
                                onClicked: root.work.run("assistant.auto", {token: root.preview.token, confirmed: true})
                            }
                            ActionButton {
                                objectName: "assistantReject"
                                text: "拒绝建议"
                                enabled: root.work && !root.work.busy
                                onClicked: root.work.run("assistant.reject", {token: root.preview.token})
                            }
                        }
                    }
                    PaperCard {
                        Layout.fillWidth: true
                        visible: root.work && Object.keys(root.work.receipt || {}).length > 0
                        BodyText { text: "这次操作的结果"; font.pixelSize: 20; font.weight: Font.DemiBold }
                        BodyText { objectName: "assistantReceipt"; text: root.receiptText() }
                    }
                }
            }
        }

        PaperCard {
            objectName: "assistantComposer"
            Layout.fillWidth: true
            padding: 10
            paperColor: Theme.paper
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                FormArea {
                    id: question
                    objectName: "assistantQuestion"
                    accessibleLabel: "学习问题或操作意图，回车发送，Shift 加回车换行"
                    placeholderText: "聊聊你的学习问题…"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 62
                    enabled: !!root.work
                    onTextChanged: root.invalidate()
                    Keys.priority: Keys.BeforeItem
                    Keys.onReturnPressed: event => root.handleSendKey(event)
                    Keys.onEnterPressed: event => root.handleSendKey(event)
                }
                ActionButton {
                    objectName: "assistantAsk"
                    text: "发送"
                    primary: true
                    Layout.alignment: Qt.AlignBottom
                    enabled: root.work && !root.work.busy && question.text.trim().length > 0
                    onClicked: root.askQuestion()
                }
            }
            Flow {
                Layout.fillWidth: true
                spacing: 6
                CheckBox {
                    id: external
                    objectName: "assistantExternal"
                    checked: true
                    text: "优先使用外部模型"
                    enabled: root.work && !root.work.busy
                    Accessible.name: text
                    onToggled: root.invalidate()
                    ToolTip.visible: hovered
                    ToolTip.text: "发送时优先使用已配置的外部模型，不可用时回到本地回答。取消勾选即可只使用本地回答；材料不会随问答发送。"
                }
                ActionButton {
                    objectName: "assistantAttach"
                    text: root.attachments.length > 0 ? "材料 " + root.attachments.length : "添加材料"
                    enabled: root.work && !root.work.busy
                    onClicked: files.open()
                }
                ActionButton {
                    text: "清空材料"
                    visible: root.attachments.length > 0
                    enabled: root.work && !root.work.busy
                    onClicked: { root.attachments = []; root.invalidate() }
                }
                ActionButton {
                    objectName: "assistantPreview"
                    text: "预览安排"
                    enabled: root.work && !root.work.busy && (question.text.trim().length > 0 || root.attachments.length > 0)
                    onClicked: { root.followLatest = true; root.work.run("assistant.preview", root.payload()); root.scrollToLatest() }
                }
            }
            BodyText {
                objectName: "assistantAttachments"
                visible: root.attachments.length > 0
                text: root.attachments.map(path => decodeURIComponent(path.split("/").pop())).join("、") + " · 材料用于安排预览"
                color: Theme.muted
                font.pixelSize: Theme.captionFontSize
            }
            RowLayout {
                Layout.fillWidth: true
                BodyText {
                    objectName: "assistantModelStatus"
                    Layout.fillWidth: true
                    text: root.modelStatus
                    font.pixelSize: Theme.captionFontSize
                    color: Theme.muted
                    wrapMode: Text.Wrap
                }
                ActionButton {
                    objectName: "assistantModelSettings"
                    text: "模型设置"
                    onClicked: root.bridge.navigate("settings")
                }
            }
            BodyText { text: "Enter 发送 · Shift+Enter 换行"; color: Theme.muted; font.pixelSize: 12 }
        }
    }


    FileDialog {
        id: files
        title: "选择学习材料"
        fileMode: FileDialog.OpenFiles
        nameFilters: ["学习材料 (*.pdf *.png *.jpg *.jpeg *.webp *.txt *.md *.docx *.pptx *.xlsx)", "所有文件 (*)"]
        onAccepted: { root.attachments = Array.from(new Set(root.attachments.concat(selectedFiles.map(url => url.toString())))); root.invalidate() }
    }

    Dialog {
        id: moreActionsDialog
        objectName: "assistantMoreActionsDialog"
        title: "更多操作"
        anchors.centerIn: parent
        modal: true
        width: Math.max(280, Math.min(root.width - 32, 600))
        height: Math.max(300, Math.min(root.height - 32, 640))
        standardButtons: Dialog.Close
        contentItem: ScrollView {
            id: moreActionsScroll
            clip: true
            contentWidth: availableWidth
            contentHeight: moreActionsColumn.implicitHeight + 8
            ScrollBar.vertical.policy: ScrollBar.AsNeeded

            ColumnLayout {
                id: moreActionsColumn
                width: moreActionsScroll.availableWidth
                spacing: 10

                PaperCard {
                    Layout.fillWidth: true
                    padding: 12
                    BodyText { text: "撤销与预警"; font.pixelSize: 20; font.weight: Font.DemiBold }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 7
                        ActionButton {
                            objectName: "assistantUndoPreview"
                            text: "检查可撤销操作"
                            enabled: root.work && !root.work.busy
                            onClicked: root.work.run("assistant.undoPreview", {})
                        }
                        ActionButton {
                            objectName: "assistantAlerts"
                            text: "查看知识预警"
                            onClicked: {
                                moreActionsDialog.close()
                                if (root.bridge) root.bridge.navigate("knowledge")
                            }
                        }
                    }
                    PaperCard {
                        Layout.fillWidth: true
                        visible: !!root.undo.token
                        paperColor: Theme.peach
                        BodyText { text: "撤销复核"; font.pixelSize: Theme.bodyFontSize; font.weight: Font.DemiBold }
                        BodyText {
                            text: [(root.undo.review || {}).label || "", (root.undo.review || {}).message || "",
                                  (root.undo.review || {}).action_id ? "操作编号：" + root.undo.review.action_id : ""].filter(Boolean).join("\n")
                            wrapMode: Text.Wrap
                        }
                        CheckBox {
                            id: undoReviewed
                            objectName: "assistantUndoReviewed"
                            text: "我已复核将恢复的内容，确认撤销"
                            Accessible.name: text
                        }
                        ActionButton {
                            objectName: "assistantUndo"
                            text: "确认撤销"
                            enabled: root.work && !root.work.busy && undoReviewed.checked
                            onClicked: {
                                root.work.run("assistant.undo", {token: root.undo.token, confirmed: true})
                                moreActionsDialog.close()
                            }
                        }
                    }
                }

                PaperCard {
                    Layout.fillWidth: true
                    padding: 10
                    CheckBox {
                        id: policyDisclosure
                        objectName: "assistantPolicyDisclosure"
                        text: "执行策略"
                        Accessible.name: "展开学习助理执行策略"
                    }
                    ColumnLayout {
                        visible: policyDisclosure.checked
                        Layout.fillWidth: true
                        spacing: 5
                        Repeater {
                            model: moreActionsDialog.opened && policyDisclosure.checked ? (root.assistantState.policyActions || []) : []
                            RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                BodyText { Layout.fillWidth: true; text: modelData.label + (modelData.enabled ? " · 已启用" : " · 未启用") }
                                ActionButton {
                                    text: modelData.enabled ? "停用" : "启用"
                                    enabled: root.work && !root.work.busy
                                    onClicked: {
                                        root.pendingPolicy = modelData.key
                                        root.pendingPolicyLabel = modelData.label
                                        root.pendingPolicyValue = !modelData.enabled
                                        moreActionsDialog.close()
                                        policyDialog.open()
                                    }
                                }
                            }
                        }
                    }
                }

                PaperCard {
                    Layout.fillWidth: true
                    padding: 10
                    CheckBox {
                        id: auditDisclosure
                        objectName: "assistantAuditDisclosure"
                        text: "操作审计（" + String((root.assistantState.journal || []).length) + "）"
                        Accessible.name: "展开学习助理操作审计"
                    }
                    ColumnLayout {
                        visible: auditDisclosure.checked
                        Layout.fillWidth: true
                        spacing: 5
                        BodyText {
                            visible: !(root.assistantState.journal || []).length
                            text: "尚无学习助理操作记录。"
                            color: Theme.muted
                        }
                        Repeater {
                            model: moreActionsDialog.opened && auditDisclosure.checked ? (root.assistantState.journal || []) : []
                            ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                BodyText { text: modelData.action_type + " · " + modelData.status + " · " + modelData.created_at; font.pixelSize: Theme.captionFontSize }
                                BodyText { text: modelData.message || modelData.action_id; color: Theme.muted; font.pixelSize: Theme.captionFontSize; wrapMode: Text.Wrap }
                                ActionButton {
                                    text: "重新预览"
                                    visible: modelData.status === "local_commit_failed" || modelData.status === "business_invalid"
                                    enabled: root.work && !root.work.busy
                                    onClicked: root.work.run("assistant.retry", {actionId: modelData.action_id})
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    Dialog {
        id: policyDialog
        objectName: "assistantPolicyDialog"
        title: "确认执行策略变更"
        anchors.centerIn: parent
        modal: true
        width: Math.min(root.width - 40, 480)
        standardButtons: Dialog.Ok | Dialog.Cancel
        contentItem: BodyText { text: (root.pendingPolicyValue ? "启用" : "停用") + "策略：" + root.pendingPolicyLabel + "。" }
        onAccepted: if (root.work) root.work.run("assistant.policy", {action: root.pendingPolicy, enabled: root.pendingPolicyValue, confirmed: true})
    }
}

