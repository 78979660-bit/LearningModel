import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import ".."
import "../components"

ScrollView {
    id: root
    onVisibleChanged: if (!visible) outlineFileDialog.close()
    objectName: "subjectsPane"
    required property var work

    readonly property var subjectsData: subjectsSnapshot.value
    readonly property bool initialDataReady: !work || subjectsSnapshot.ready || !!work.error
    SnapshotView {
        id: subjectsSnapshot
        objectName: "subjectsSnapshot"
        source: root.work
        dataProperty: "subjectsData"
        pageVisible: root.visible
    }
    readonly property var subjectRows: list(subjectsData.subjects)
    readonly property var preview: work ? work.subjectPreview : null
    readonly property var result: work ? (work.subjectResult || null) : null
    readonly property bool busy: work ? Boolean(work.busy) : false
    readonly property string errorMessage: work ? String(work.error || "") : ""
    readonly property string statusMessage: work ? String(work.message || "") : ""
    property string selectedSubjectKey: ""
    property string requestedKind: ""
    property string importedFilename: ""

    contentWidth: availableWidth
    contentHeight: body.implicitHeight + 28
    clip: true

    function list(value) {
        if (!value) return []
        if (Array.isArray(value)) return value
        if (typeof value.length === "number") return value
        return []
    }

    function invalidateSubjectPreview() {
        if (work && work.invalidatePreview)
            work.invalidatePreview("subject")
        if (preview && preview.token)
            clearAcknowledgement()
    }

    function clearAcknowledgement() {
        acknowledgement.checked = false
        confirmationName.text = ""
    }

    function applyOutlineDraft(draft) {
        if (!draft) return
        invalidateSubjectPreview()
        requestedKind = "introduce"
        selectedSubjectKey = ""
        clearAcknowledgement()
        if (draft.name !== undefined && draft.name !== null)
            subjectName.text = String(draft.name)
        subjectOutline.text = String(draft.outline || "")
        importedFilename = String(draft.filename || "")
    }

    function requestIntroducePreview() {
        invalidateSubjectPreview()
        requestedKind = "introduce"
        selectedSubjectKey = ""
        clearAcknowledgement()
        if (work)
            work.run("subject.preview", {
                kind: "introduce",
                name: subjectName.text,
                outline: subjectOutline.text,
                subjectKey: ""
            })
    }

    function requestLifecyclePreview(kind, subject) {
        invalidateSubjectPreview()
        if (selectedSubjectKey !== String(subject.subjectKey || ""))
            clearAcknowledgement()
        selectedSubjectKey = String(subject.subjectKey || "")
        requestedKind = kind
        clearAcknowledgement()
        if (work)
            work.run("subject.preview", {
                kind: kind,
                name: "",
                outline: "",
                subjectKey: selectedSubjectKey
            })
    }

    function confirmPreview() {
        if (!work || !preview || !preview.token)
            return
        work.run("subject.confirm", {
            token: preview.token,
            typedName: confirmationName.text,
            confirmed: true
        })
    }

    GridLayout {
        id: body
        width: root.availableWidth - 32
        x: 16
        y: 14
        columns: width >= 980 ? 2 : 1
        rowSpacing: 8
        columnSpacing: 10

        PaperCard {
            Layout.fillWidth: true
            Layout.columnSpan: body.columns
            paperColor: Theme.lavender
            BodyText {
                text: "学科管理"
                font.pixelSize: 22
                font.weight: Font.DemiBold
            }
            BodyText {
                visible: false; text: ""
                color: Theme.muted
            }
            BodyText {
                objectName: "subjectCatalogRevision"
                text: "当前目录修订：" + String((root.subjectsData || {}).catalogRevision || 0)
                color: Theme.muted
                font.pixelSize: Theme.captionFontSize
            }
        }

        PaperCard {
            Layout.fillWidth: true
            Layout.minimumWidth: body.columns === 2 ? 360 : 0
            BodyText { text: "引入学科"; font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
            BodyText { text: "每行一个“章节 / 知识点”"; color: Theme.muted; font.pixelSize: Theme.captionFontSize }
            FormField {
                id: subjectName
                objectName: "subjectNameInput"
                accessibleLabel: "新学科全名"
                placeholderText: "例如：数据结构"
                enabled: !root.busy
                onTextEdited: root.invalidateSubjectPreview()
            }
            TextArea {
                id: subjectOutline
                objectName: "subjectOutlineInput"
                Accessible.name: "新学科章节与知识点目录，每行格式为章节斜线知识点"
                Layout.fillWidth: true
                Layout.preferredHeight: 112
                enabled: !root.busy
                placeholderText: "线性表 / 顺序表\n线性表 / 链表"
                wrapMode: TextEdit.Wrap
                selectByMouse: true
                color: Theme.ink
                font.family: Theme.fontFamily
                font.pixelSize: Theme.bodyFontSize
                background: Rectangle {
                    radius: 8
                    color: Theme.paper
                    border.width: 1
                    border.color: subjectOutline.activeFocus ? Theme.accent : Theme.line
                }
                onTextChanged: root.invalidateSubjectPreview()
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                ActionButton {
                    objectName: "subjectImportOutlineButton"
                    text: "从文件导入目录"
                    enabled: !root.busy
                    onClicked: outlineFileDialog.open()
                }
                BodyText {
                    visible: root.importedFilename.length > 0
                    text: "已载入草稿：" + root.importedFilename
                    color: Theme.muted
                    font.pixelSize: Theme.captionFontSize
                }
            }
            BodyText { text: "TXT、MD、JSON，最多 300 KB"; color: Theme.muted; font.pixelSize: Theme.captionFontSize }
            ActionButton {
                objectName: "subjectPreviewIntroduceButton"
                text: "预览引入影响"
                primary: true
                enabled: !root.busy && subjectName.text.trim().length > 0 && subjectOutline.text.trim().length > 0
                onClicked: root.requestIntroducePreview()
            }
        }

        PaperCard {
            Layout.fillWidth: true
            Layout.minimumWidth: body.columns === 2 ? 360 : 0
            BodyText { text: "已有学科"; font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
            BodyText {
                text: "归档会保留记录；恢复不会重新启用已归档计划。"
                color: Theme.muted
                font.pixelSize: Theme.captionFontSize
            }
            BodyText {
                visible: root.subjectRows.length === 0
                text: "当前没有已登记的学科。"
                color: Theme.muted
            }
            Repeater {
                model: root.subjectRows
                delegate: Rectangle {
                    required property var modelData
                    objectName: "subjectRow_" + String(modelData.subjectKey || "")
                    Layout.fillWidth: true
                    implicitHeight: rowLayout.implicitHeight + 20
                    radius: 10
                    color: root.selectedSubjectKey === String(modelData.subjectKey || "") ? Theme.accentSoft : Theme.paper
                    border.width: 1
                    border.color: Theme.line

                    RowLayout {
                        id: rowLayout
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 10

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4
                            BodyText {
                                text: String(modelData.displayName || modelData.name || "未命名学科")
                                font.weight: Font.DemiBold
                            }
                            BodyText {
                                text: (String(modelData.lifecycleStatus) === "active" ? "使用中" : "已归档")
                                      + " · " + String(modelData.moduleCount || 0) + " 章 / "
                                      + String(modelData.topicCount || 0) + " 个知识点"
                                color: Theme.muted
                                font.pixelSize: Theme.captionFontSize
                            }
                            BodyText {
                                visible: root.capabilityText(modelData.capabilities).length > 0
                                text: root.capabilityText(modelData.capabilities)
                                color: Theme.muted
                                font.pixelSize: Theme.captionFontSize
                            }
                        }
                        ActionButton {
                            objectName: "subjectArchivePreview_" + String(modelData.subjectKey || "")
                            visible: String(modelData.lifecycleStatus) === "active"
                            text: "预览归档"
                            enabled: !root.busy
                            accessibleLabel: "预览归档学科 " + String(modelData.name || "") + "，保留历史记录"
                            onClicked: root.requestLifecyclePreview("remove", modelData)
                        }
                        ActionButton {
                            objectName: "subjectRestorePreview_" + String(modelData.subjectKey || "")
                            visible: String(modelData.lifecycleStatus) === "archived"
                            text: "预览恢复"
                            enabled: !root.busy
                            accessibleLabel: "预览恢复学科 " + String(modelData.name || "")
                            onClicked: root.requestLifecyclePreview("restore", modelData)
                        }
                    }
                }
            }
        }

        PaperCard {
            objectName: "subjectManagementAuditCard"
            Layout.fillWidth: true
            Layout.columnSpan: body.columns
            visible: root.list((root.subjectsData || {}).operations).length > 0
            BodyText { text: "学科操作记录"; font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
            Repeater {
                model: root.list((root.subjectsData || {}).operations)
                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: auditRow.implicitHeight + 16
                    radius: 8
                    color: Theme.paper
                    border.width: 1
                    border.color: Theme.line
                    RowLayout {
                        id: auditRow
                        anchors.fill: parent
                        anchors.margins: 8
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3
                            BodyText { text: String(modelData.label || "学科操作") + " · " + String(modelData.name || "") }
                            BodyText {
                                text: "数据库：" + root.operationStatusText(modelData.status)
                                      + " · 目录：" + root.projectionStatusText(modelData.projectionStatus)
                                color: Theme.muted
                                font.pixelSize: Theme.captionFontSize
                            }
                            BodyText {
                                text: "影响：记录 " + String(modelData.recordCount || 0) + " 条，计划 "
                                      + String(modelData.planCount || 0) + " 个；目录修订 "
                                      + String(modelData.expectedCatalogRevision || 0)
                                color: Theme.muted
                                font.pixelSize: Theme.captionFontSize
                            }
                        }
                        ActionButton {
                            objectName: "subjectAuditRetry_" + String(modelData.operationId || "")
                            visible: Boolean(modelData.canRetryProjection && modelData.operationId)
                            text: "重试目录更新"
                            enabled: !root.busy
                            onClicked: if (root.work) root.work.run("subject.retryProjection", {operationId: modelData.operationId})
                        }
                    }
                }
            }
        }

        PaperCard {
            objectName: "subjectLifecycleHistoryCard"
            Layout.fillWidth: true
            Layout.columnSpan: body.columns
            visible: root.list((root.subjectsData || {}).history).length > 0
            BodyText { text: "生命周期历史"; font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
            Repeater {
                model: root.list((root.subjectsData || {}).history)
                delegate: BodyText {
                    required property var modelData
                    text: String(modelData.name || "") + " · "
                          + root.lifecycleStatusText(modelData.fromStatus) + " → "
                          + root.lifecycleStatusText(modelData.toStatus) + " · "
                          + String(modelData.reason || "") + " · " + String(modelData.createdAt || "")
                    color: Theme.muted
                    font.pixelSize: Theme.captionFontSize
                }
            }
        }

        PaperCard {
            objectName: "subjectPreviewCard"
            Layout.fillWidth: true
            Layout.columnSpan: body.columns
            paperColor: Theme.peach
            visible: Boolean(root.preview && root.preview.token)
            BodyText { text: "操作影响预览"; font.pixelSize: Theme.sectionFontSize; font.weight: Font.DemiBold }
            BodyText {
                text: String(root.preview ? (root.preview.label || "学科操作") : "") + "：" + String(root.preview ? (root.preview.name || "") : "")
                font.weight: Font.DemiBold
            }
            BodyText {
                visible: Boolean(root.preview && String(root.preview.kind) === "introduce")
                text: "将新增 " + String((root.preview || {}).moduleCount || 0) + " 个章节、" + String((root.preview || {}).topicCount || 0) + " 个知识点。"
            }
            BodyText {
                visible: Boolean(root.preview && String(root.preview.kind) !== "introduce")
                text: "现有学习记录 " + String(((root.preview || {}).impact || {}).records || 0) + " 条会保留；关联的活动计划 " + String(((root.preview || {}).impact || {}).plans || 0) + " 个。"
            }
            BodyText {
                visible: Boolean(root.preview && String(root.preview.kind) === "remove")
                text: "确认后，关联的活动计划将归档。记录与目录历史不会删除。"
                color: Theme.warning
            }
            BodyText {
                visible: Boolean(root.preview && String(root.preview.kind) === "restore")
                text: "恢复使用同一个学科身份；已归档计划仍保持归档状态。"
            }
            BodyText {
                visible: Boolean(root.preview && (((root.preview || {}).impact || {}).planScopes || []).length > 0)
                text: "关联计划范围：" + (((root.preview || {}).impact || {}).planScopes || []).join("、")
                color: Theme.muted
            }
            BodyText { text: "输入学科全名以确认。"; color: Theme.muted }
            FormField {
                id: confirmationName
                objectName: "subjectConfirmationNameInput"
                accessibleLabel: "确认操作的学科全名"
                placeholderText: "准确输入：" + String(root.preview ? (root.preview.name || "学科全名") : "学科全名")
                enabled: !root.busy
            }
            CheckBox {
                id: acknowledgement
                objectName: "subjectImpactAcknowledgement"
                Accessible.name: "我已阅读学科操作影响范围并确认执行"
                Layout.fillWidth: true
                enabled: !root.busy
                text: "我已阅读影响范围，并确认按此预览执行。"
                font.family: Theme.fontFamily
                font.pixelSize: Theme.bodyFontSize
            }
            ActionButton {
                objectName: "subjectConfirmButton"
                text: "确认并执行"
                primary: true
                enabled: Boolean(!root.busy && root.preview && root.preview.token
                                 && acknowledgement.checked
                                 && confirmationName.text === String(root.preview.name || ""))
                onClicked: root.confirmPreview()
            }
        }

        PaperCard {
            objectName: "subjectResultCard"
            Layout.fillWidth: true
            Layout.columnSpan: body.columns
            paperColor: Theme.pinkSoft
            visible: Boolean(root.result && root.result.saved)
            BodyText { text: String((root.result || {}).message || "学科操作已保存。") }
            BodyText {
                visible: Boolean((root.result || {}).warning)
                text: String((root.result || {}).warning || "")
                color: Theme.warning
            }
            ActionButton {
                objectName: "subjectRetryProjectionButton"
                visible: Boolean((root.result || {}).projectionPending && (root.result || {}).operationId)
                text: "重试目录更新"
                enabled: !root.busy
                onClicked: if (root.work) root.work.run("subject.retryProjection", {operationId: root.result.operationId})
            }
        }

        PaperCard {
            objectName: "subjectStatusCard"
            Layout.fillWidth: true
            Layout.columnSpan: body.columns
            visible: root.errorMessage.length > 0 || root.statusMessage.length > 0 || root.busy
            BodyText { visible: root.busy; text: "正在处理学科操作…"; color: Theme.muted }
            BodyText { visible: root.errorMessage.length > 0; text: root.errorMessage; color: Theme.warning }
            BodyText { visible: root.statusMessage.length > 0; text: root.statusMessage; color: Theme.muted }
        }
    }

    function capabilityText(capabilities) {
        if (!capabilities) return ""
        const labels = {
            study_plan: "学习计划",
            generic_practice: "通用练习",
            programming_practice: "编程练习",
            objective_practice: "客观题练习"
        }
        return Object.keys(capabilities).filter(function(key) { return Boolean(capabilities[key]) })
            .map(function(key) { return labels[key] || key }).join("、")
    }

    function operationStatusText(status) {
        const labels = {
            prepared: "已预览",
            approved: "待执行",
            db_committed_projection_pending: "已保存",
            completed: "已完成",
            failed_before_commit: "未写入",
            failed: "失败"
        }
        return labels[String(status)] || String(status || "未记录")
    }

    function projectionStatusText(status) {
        const labels = { pending: "待更新", failed: "更新失败，可重试", published: "已更新", superseded: "已由新目录接替" }
        return labels[String(status)] || (status ? String(status) : "无需更新")
    }

    function lifecycleStatusText(status) {
        const labels = { absent: "未登记", active: "使用中", archived: "已归档" }
        return labels[String(status)] || String(status || "未知")
    }

    FileDialog {
        id: outlineFileDialog
        objectName: "subjectOutlineFileDialog"
        title: "选择目录草稿"
        fileMode: FileDialog.OpenFile
        nameFilters: ["目录文件 (*.txt *.md *.json)", "所有文件 (*)"]
        onAccepted: {
            if (root.work)
                root.work.run("subject.importOutline", {url: selectedFile.toString()})
        }
    }

    Connections {
        target: root.work
        function onOutlineDraftChanged() {
            root.applyOutlineDraft(root.work ? root.work.outlineDraft : null)
        }
        function onPreviewsChanged() {
            if (!root.preview || !root.preview.token)
                root.clearAcknowledgement()
        }
    }
}
