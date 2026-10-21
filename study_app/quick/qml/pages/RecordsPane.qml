import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import ".."
import "../components"

ScrollView {
    id: root
    objectName: "recordsPane"
    required property var workspace
    readonly property var data: recordsSnapshot.value
    readonly property bool initialDataReady: !workspace || recordsSnapshot.ready || !!workspace.error
    SnapshotView {
        id: recordsSnapshot
        objectName: "recordsSnapshot"
        source: root.workspace
        dataProperty: "recordsData"
        pageVisible: root.visible
    }
    readonly property var catalog: data.catalog || ({})
    readonly property var draft: workspace ? workspace.recordPreview : ({})
    property var attachmentUrls: []
    property var searchFileTokens: []
    property var reviewedProblems: []
    property var reviewEdits: ({})
    property bool ready: false
    onVisibleChanged: if (!visible && ready) filesDialog.close()
    property string prefillNote: ""
    function applyPrefill(value) {
        if (ready && value.length > 0) {
            note.text = value
            root.invalidate()
        }
    }
    onPrefillNoteChanged: applyPrefill(prefillNote)
    contentWidth: availableWidth
    contentHeight: body.implicitHeight + 20
    clip: true

    function invalidate() { if (ready && workspace) workspace.invalidatePreview("record") }
    function payload(mode) {
        return {date: recordDate.text, subject: subject.currentText, module: module.currentText,
            topic: topic.editable ? topic.editText : topic.currentText,
            activity: activity.currentValue, source: source.currentValue,
            score: score.text.trim() ? Number(score.text) : null,
            duration_minutes: duration.text.trim() ? Number(duration.text) : null,
            note: note.text, answer_result: answerResult.text, error_cause: errorCause.text,
            attachments: attachmentUrls, search_file_tokens: searchFileTokens, recognition: mode}
    }
    function updateProblem(index, key, value) {
        const edits = Object.assign({}, reviewEdits)
        const item = Object.assign({}, edits[index] || {})
        item[key] = value
        edits[index] = item
        reviewEdits = edits
        reviewed.checked = false
    }
    Component.onCompleted: {
        if (prefillNote.length > 0) note.text = prefillNote
        if (workspace) workspace.invalidatePreview("record")
        ready = true
    }
    Connections {
        target: root.workspace
        function onPreviewsChanged() {
            reviewed.checked = false
            root.reviewedProblems = (root.draft.draft || {}).problems || []
            root.reviewEdits = ({})
        }
    }
    ColumnLayout {
        id: body
        width: root.availableWidth
        spacing: 14
        PaperCard {
            Layout.fillWidth: true
            BodyText { text: "新增学习记录"; font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
            BodyText { visible: false; text: ""; color: Theme.muted }
            BodyText { text: "日期" }
            FormField {
                id: recordDate; objectName: "recordDate"
                accessibleLabel: "学习记录日期"
                text: new Date().toLocaleDateString(Qt.locale("en_CA"), "yyyy-MM-dd")
                placeholderText: "YYYY-MM-DD"
                enabled: root.workspace && !root.workspace.busy
                onTextEdited: root.invalidate()
            }
            BodyText { text: "学科" }
            FormChoice {
                id: subject; objectName: "recordSubject"; accessibleLabel: "记录学科"
                model: (root.catalog.subjects || []).map(s => s.name)
                enabled: root.workspace && !root.workspace.busy
                onActivated: root.invalidate()
            }
            BodyText { text: "模块" }
            FormChoice {
                id: module; objectName: "recordModule"; accessibleLabel: "记录模块"
                model: [""].concat((root.catalog.modules || []).filter(m => m.subject_name === subject.currentText).map(m => m.module_name))
                enabled: root.workspace && !root.workspace.busy
                onActivated: root.invalidate()
            }
            BodyText { text: "知识点（可选择或填写）" }
            FormChoice {
                id: topic; objectName: "recordTopic"; accessibleLabel: "记录知识点"
                editable: true
                model: [""].concat((root.catalog.topics || []).filter(t => t.subject_name === subject.currentText && (!module.currentText || t.module_name === module.currentText)).map(t => t.topic_name))
                enabled: root.workspace && !root.workspace.busy
                onActivated: root.invalidate()
                onEditTextChanged: root.invalidate()
            }
            BodyText { text: "活动与来源" }
            FormChoice {
                id: activity; objectName: "recordActivity"; accessibleLabel: "学习活动"
                textRole: "label"; valueRole: "value"
                model: [{label:"复习",value:"review"},{label:"做题",value:"exercise"},{label:"课堂学习",value:"class"},{label:"复习并练习",value:"review_exercise"},{label:"自测",value:"self_test"}]
                enabled: root.workspace && !root.workspace.busy
                onActivated: root.invalidate()
            }
            FormChoice {
                id: source; objectName: "recordSource"; accessibleLabel: "材料来源"
                textRole: "label"; valueRole: "value"
                model: [{label:"课外",value:"outside_class"},{label:"课堂",value:"classroom"},{label:"自学",value:"self_study"}]
                enabled: root.workspace && !root.workspace.busy
                onActivated: root.invalidate()
            }
            BodyText { text: "分数（0–100，可留空）" }
            FormField {
                id: score; objectName: "recordScore"; accessibleLabel: "记录分数"
                validator: DoubleValidator { bottom: 0; top: 100; decimals: 2; notation: DoubleValidator.StandardNotation; locale: "C" }
                enabled: root.workspace && !root.workspace.busy
                onTextEdited: root.invalidate()
            }
            BodyText { text: "学习时长（分钟，可留空）" }
            FormField {
                id: duration; objectName: "recordDuration"; accessibleLabel: "学习时长，分钟"
                validator: DoubleValidator { bottom: 0; decimals: 2; notation: DoubleValidator.StandardNotation; locale: "C" }
                enabled: root.workspace && !root.workspace.busy
                onTextEdited: root.invalidate()
            }
            BodyText { text: "学习内容与备注" }
            FormArea { id: note; objectName: "recordNote"; accessibleLabel: "学习内容与备注"; enabled: root.workspace && !root.workspace.busy; onTextChanged: root.invalidate() }
            BodyText { text: "做题情况" }
            FormArea { id: answerResult; objectName: "recordAnswerResult"; accessibleLabel: "做题情况"; enabled: root.workspace && !root.workspace.busy; onTextChanged: root.invalidate() }
            BodyText { text: "错因" }
            FormArea { id: errorCause; objectName: "recordErrorCause"; accessibleLabel: "错因"; enabled: root.workspace && !root.workspace.busy; onTextChanged: root.invalidate() }
            ActionButton {
                objectName: "recordAddFiles"
                text: "选择附件"
                enabled: root.workspace && !root.workspace.busy
                onClicked: filesDialog.open()
            }
            FormField {
                id: fileHint
                objectName: "recordFileHint"
                accessibleLabel: "材料文件描述"
                placeholderText: "填写文件或所在位置的描述"
                enabled: root.workspace && !root.workspace.busy
            }
            ActionButton {
                objectName: "recordSearchFiles"
                text: "按描述查找本地文件"
                enabled: root.workspace && !root.workspace.busy && fileHint.text.trim().length > 0
                onClicked: root.workspace.run("record.search", {query: fileHint.text})
            }
            Repeater {
                model: root.workspace ? (root.workspace.fileSearch.files || []) : []
                CheckBox {
                    required property var modelData
                    Layout.fillWidth: true
                    text: modelData.file_name + " · " + String(modelData.reason || "")
                    Accessible.name: "加入材料：" + modelData.file_name
                    checked: root.searchFileTokens.indexOf(modelData.file_token) >= 0
                    enabled: root.workspace && !root.workspace.busy
                    onClicked: {
                        const next = root.searchFileTokens.filter(t => t !== modelData.file_token)
                        if (checked) next.push(modelData.file_token)
                        root.searchFileTokens = next
                        root.invalidate()
                    }
                    contentItem: Text { text: parent.text; font: parent.font; color: Theme.ink; textFormat: Text.PlainText; wrapMode: Text.Wrap; leftPadding: parent.indicator.width + parent.spacing }
                }
            }
            Repeater {
                model: root.attachmentUrls
                RowLayout {
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    BodyText { text: decodeURIComponent(String(modelData).split("/").pop()); font.pixelSize: Theme.captionFontSize }
                    ActionButton {
                        text: "移除附件"
                        enabled: root.workspace && !root.workspace.busy
                        onClicked: {
                            const values = root.attachmentUrls.slice()
                            values.splice(index, 1)
                            root.attachmentUrls = values
                            root.invalidate()
                        }
                    }
                }
            }
            CheckBox {
                id: enhanced; objectName: "recordEnhanced"
                text: "明确使用增强识别（可能发送材料内容）"
                Accessible.name: text
                enabled: root.workspace && !root.workspace.busy
                onToggled: root.invalidate()
            }
            Flow {
                Layout.fillWidth: true
                spacing: 8
                ActionButton {
                    objectName: "previewRecord"
                    text: "预览手动记录"
                    enabled: root.workspace && !root.workspace.busy && subject.currentText.length > 0
                    onClicked: root.workspace.run("record.preview", root.payload("manual"))
                }
                ActionButton {
                    objectName: "recognizeRecord"
                    text: enhanced.checked ? "增强识别并预览" : "本地识别并预览"
                    enabled: root.workspace && !root.workspace.busy && subject.currentText.length > 0
                    onClicked: root.workspace.run("record.preview", root.payload(enhanced.checked ? "enhanced" : "local"))
                }
                ActionButton {
                    text: "清空草稿"
                    enabled: root.workspace && !root.workspace.busy
                    onClicked: {
                        root.invalidate(); note.text = ""; answerResult.text = ""; errorCause.text = ""
                        score.text = ""; duration.text = ""; root.attachmentUrls = []; root.searchFileTokens = []; root.reviewedProblems = []
                    }
                }
            }
        }
        PaperCard {
            Layout.fillWidth: true
            visible: !!root.draft.token
            BodyText { text: "复核识别草稿"; font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
            BodyText {
                text: String((root.draft.recognition || {}).mode || "") + " · " + root.reviewedProblems.length + " 个题目\n" + (root.draft.warnings || []).join("\n")
                color: Theme.muted
            }
            BodyText {
                readonly property var evidence: (root.draft.recognition || {}).evidence || ({})
                text: "识别依据：" + String(evidence.source || "手动") + " · 附件 " + Number(evidence.attachment_count || 0) + " · 题面块 " + Number(evidence.problem_blocks || 0) + (evidence.extracted_text ? " · 已提取材料文本" : "")
                color: Theme.muted; font.pixelSize: Theme.captionFontSize
            }
            BodyText {
                readonly property var record: root.draft.draft || ({})
                text: String(record.date || "") + " · " + String(record.subject || "") + " / " + String(record.topic || "") + "\n分数：" + String(record.score === null || record.score === undefined ? "未填写" : record.score)
            }
            Repeater {
                model: root.reviewedProblems
                ColumnLayout {
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    BodyText { text: "题目 " + (index + 1) + " · 来源 " + String(modelData.parser_source || modelData.statement_source || "用户输入"); font.weight: Font.DemiBold }
                    FormField {
                        objectName: "review-title-" + index
                        accessibleLabel: "第" + (index + 1) + "题标题"
                        text: String(modelData.title || "")
                        onTextEdited: root.updateProblem(index, "title", text)
                    }
                    FormArea {
                        objectName: "review-statement-" + index
                        accessibleLabel: "第" + (index + 1) + "题题面"
                        text: String(modelData.statement || "")
                        onTextChanged: if (activeFocus) root.updateProblem(index, "statement", text)
                    }
                    FormField {
                        accessibleLabel: "第" + (index + 1) + "题作答结果"
                        placeholderText: "作答结果"
                        text: String(modelData.answer_result || "")
                        onTextEdited: root.updateProblem(index, "answer_result", text)
                    }
                    FormField {
                        accessibleLabel: "第" + (index + 1) + "题错因"
                        placeholderText: "错因"
                        text: String(modelData.error_cause || "")
                        onTextEdited: root.updateProblem(index, "error_cause", text)
                    }
                    BodyText { text: "识别状态：" + String(modelData.status || "未判定") + " · 正确率 " + String(modelData.correctness === null || modelData.correctness === undefined ? "未知" : modelData.correctness) + " · 难度 " + String(modelData.difficulty_score === null || modelData.difficulty_score === undefined ? "未知" : modelData.difficulty_score); font.pixelSize: Theme.captionFontSize }
                    FormChoice {
                        objectName: "review-status-" + index
                        accessibleLabel: "第" + (index + 1) + "题复核状态"
                        model: ["unknown", "correct", "partial", "wrong"]
                        currentIndex: Math.max(0, model.indexOf(modelData.status || "unknown"))
                        onActivated: {
                            root.updateProblem(index, "status", currentText)
                            if (currentText === "correct" || currentText === "wrong") {
                                reviewCorrectness.text = currentText === "correct" ? "100" : "0"
                                root.updateProblem(index, "correctness", currentText === "correct" ? 100 : 0)
                                root.updateProblem(index, "partial_credit", currentText === "correct" ? 1 : 0)
                            } else if (currentText === "unknown") {
                                reviewCorrectness.text = ""
                                root.updateProblem(index, "correctness", null)
                                root.updateProblem(index, "partial_credit", null)
                            }
                        }
                    }
                    BodyText { text: "复核正确率（0–100，可留空）与难度（0–100，可留空）"; font.pixelSize: Theme.captionFontSize }
                    FormField {
                        id: reviewCorrectness
                        objectName: "review-correctness-" + index
                        accessibleLabel: "第" + (index + 1) + "题复核正确率"
                        placeholderText: "正确率"
                        text: modelData.correctness === null || modelData.correctness === undefined ? "" : String(modelData.correctness)
                        validator: DoubleValidator { bottom: 0; top: 100; decimals: 2; notation: DoubleValidator.StandardNotation; locale: "C" }
                        enabled: root.workspace && !root.workspace.busy
                        onTextEdited: {
                            root.updateProblem(index, "correctness", text.trim() ? Number(text) : null)
                            root.updateProblem(index, "partial_credit", text.trim() ? Number(text) / 100 : null)
                        }
                    }
                    FormField {
                        objectName: "review-difficulty-" + index
                        accessibleLabel: "第" + (index + 1) + "题复核难度"
                        placeholderText: "难度"
                        text: modelData.difficulty_score === null || modelData.difficulty_score === undefined ? "" : String(modelData.difficulty_score)
                        validator: DoubleValidator { bottom: 0; top: 100; decimals: 2; notation: DoubleValidator.StandardNotation; locale: "C" }
                        enabled: root.workspace && !root.workspace.busy
                        onTextEdited: {
                            root.updateProblem(index, "difficulty_score", text.trim() ? Number(text) : null)
                            root.updateProblem(index, "difficulty_source", "user_review")
                        }
                    }
                }
            }
            CheckBox {
                id: reviewed; objectName: "recordReviewed"
                text: "我已复核记录事实、题面、结果与错因，确认保存"
                Accessible.name: text
                enabled: root.workspace && !root.workspace.busy
            }
            ActionButton {
                objectName: "saveReviewedRecord"
                text: "确认保存学习记录"
                primary: true
                enabled: root.workspace && !root.workspace.busy && !!root.draft.token && reviewed.checked
                onClicked: {
                    const payload = Object.assign({}, root.draft.draft)
                    payload.problems = root.reviewedProblems.map((p, index) => Object.assign({}, p, root.reviewEdits[index] || {}))
                    root.workspace.run("record.save", {token: root.draft.token, reviewed: payload})
                }
            }
        }
        PaperCard {
            Layout.fillWidth: true
            BodyText { text: "最近学习记录"; font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
            BodyText { visible: !(root.data.records || []).length; text: "暂无记录。保存后此处与首页会从实际数据库刷新。"; color: Theme.muted }
            Repeater {
                model: root.data.records || []
                BodyText {
                    required property var modelData
                    text: modelData.date + " · " + modelData.subject + " / " + (modelData.topic || modelData.module || "") + "\n" + String(modelData.note || "") + " · 题目 " + Number(modelData.problem_count || 0) + " · 附件 " + (modelData.attachments || []).map(f => f.file_name).join("、")
                    font.pixelSize: Theme.captionFontSize
                }
            }
        }
        Item { implicitHeight: 8 }
    }
    FileDialog {
        id: filesDialog
        title: "选择学习材料"
        fileMode: FileDialog.OpenFiles
        nameFilters: ["学习材料 (*.txt *.md *.pdf *.docx *.png *.jpg *.jpeg)", "所有文件 (*)"]
        onAccepted: {
            root.attachmentUrls = root.attachmentUrls.concat(selectedFiles.map(url => String(url)))
            root.invalidate()
        }
    }
}
