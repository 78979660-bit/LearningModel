import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "../components"

Item {
    id: root
    onVisibleChanged: {
        if (!visible) clearDialog.close()
        else if (ready) snapshotSync.restart()
    }
    objectName: "preferencesPane"
    property var work: null
    // Keep an owned JS snapshot and reconcile retained pages even when a read
    // returns the same DTO and therefore emits no dataChanged notification.
    property var settingsData: ({})
    readonly property var llm: settingsData.llm || ({})
    readonly property var privacy: settingsData.privacy || ({})
    readonly property var maintenance: settingsData.maintenance || ({})
    readonly property var audits: list(settingsData.audits)
    property var featureStates: ({})
    property var autoStates: ({})
    property bool syncing: false
    property bool ready: false
    property bool snapshotReady: false
    property bool draftDirty: false
    property string appliedSignature: ""
    property string activeSection: "ai"
    readonly property bool busy: work ? (work.busy || snapshotSync.running || !snapshotReady) : true
    onWorkChanged: {
        settingsData = ({})
        snapshotReady = false
        draftDirty = false
        appliedSignature = ""
        if (ready) snapshotSync.restart()
    }

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

    function stateLabel(value) {
        const labels = {
            success: "成功",
            failed: "失败",
            blocked: "已阻止",
            timed_out: "超时",
            transport_failed: "连接失败",
            parse_failed: "解析失败",
            provider_failed: "服务商失败",
            cancelled: "已取消"
        }
        return labels[String(value)] || display(value, "未知")
    }

    function mapWithFlag(source, key, value) {
        const next = {}
        const current = source || ({})
        for (const name in current) next[name] = current[name]
        next[String(key)] = value
        return next
    }

    function syncingControls(callback) {
        syncing = true
        try { callback() } finally { syncing = false }
    }

    function markDraftDirty() {
        if (!syncing && snapshotReady) draftDirty = true
    }

    function editableSnapshot(snapshot) {
        const value = snapshot.llm
        return {
            enabled: value.enabled, provider: value.provider, model: value.model,
            customBaseUrl: value.customBaseUrl, singleCallTokenLimit: value.singleCallTokenLimit,
            dailyBudgetCny: value.dailyBudgetCny, allowUploadImages: value.allowUploadImages,
            allowUploadPdfs: value.allowUploadPdfs, enabledFeatures: value.enabledFeatures,
            advisorMode: snapshot.privacy.advisorMode,
            allowExternalIntent: snapshot.privacy.allowExternalIntent, autoExec: snapshot.autoExec
        }
    }

    function syncFromSnapshot() {
        const snapshot = JSON.parse(JSON.stringify(work ? (work.settingsData || ({})) : ({})))
        if (!snapshot.llm || !snapshot.privacy || !list(snapshot.llm.providers).length) return
        if (JSON.stringify(settingsData) !== JSON.stringify(snapshot)) settingsData = snapshot
        const llmValue = snapshot.llm
        const privacyValue = snapshot.privacy
        const providerIndex = indexFor(providerChoice.model, String(llmValue.provider))
        const advisorIndex = indexFor(advisorChoice.model, String(privacyValue.advisorMode))
        if (providerIndex < 0 || advisorIndex < 0) return
        const signature = JSON.stringify(editableSnapshot(snapshot))
        // Unchanged reads and navigation must preserve deliberate user edits.
        // A stale form with no edits can safely be repaired from saved data.
        if (snapshotReady && signature === appliedSignature && draftDirty) return
        const featureMap = ({})
        list(llmValue.enabledFeatures).forEach(function(key) { featureMap[String(key)] = true })
        const autoMap = snapshot.autoExec || ({})
        syncingControls(function() {
            llmEnabled.checked = !!llmValue.enabled
            providerChoice.currentIndex = providerIndex
            modelField.text = String(llmValue.model || "")
            baseUrlField.text = String(llmValue.customBaseUrl || "")
            tokenLimitField.text = String(llmValue.singleCallTokenLimit || 8000)
            dailyBudgetField.text = String(llmValue.dailyBudgetCny === undefined ? 3 : llmValue.dailyBudgetCny)
            allowImages.checked = !!llmValue.allowUploadImages
            allowPdfs.checked = !!llmValue.allowUploadPdfs
            advisorChoice.currentIndex = advisorIndex
            externalIntent.checked = !!privacyValue.allowExternalIntent
            featureStates = featureMap
            autoStates = autoMap
        })
        appliedSignature = signature
        draftDirty = false
        snapshotReady = true
    }

    function indexFor(model, value) {
        const entries = list(model)
        for (let index = 0; index < entries.length; index++) {
            if (String(entries[index].key) === value) return index
        }
        return -1
    }

    function selectedProvider() {
        return String(providerChoice.currentValue || "local")
    }

    function credentialIdentitySaved() {
        const provider = selectedProvider()
        if (provider !== String(llm.provider || "local")) return false
        if (provider !== "custom" && provider !== "anthropic") return true
        const endpoint = String(baseUrlField.text || "").trim().replace(/\/+$/, "")
        return !llm.baseUrlNeedsReview && endpoint === String(llm.customBaseUrl || "")
    }

    function canManageCredential() {
        return work && !busy && selectedProvider() !== "local" && credentialIdentitySaved()
    }

    function saveSettings() {
        if (!work || busy) return
        if (!draftDirty) syncFromSnapshot()
        if (!snapshotReady) return
        const enabledFeatures = []
        for (let index = 0; index < featureRepeater.count; index++) {
            const item = featureRepeater.itemAt(index)
            if (item && item.checked) enabledFeatures.push(String(item.featureKey))
        }
        const autoExec = ({})
        for (let index = 0; index < autoRepeater.count; index++) {
            const item = autoRepeater.itemAt(index)
            if (item) autoExec[String(item.actionKey)] = !!item.checked
        }
        work.run("settings.save", {
            llm: {
                enabled: !!llmEnabled.checked,
                provider: selectedProvider(),
                model: String(modelField.text || "").trim(),
                customBaseUrl: String(baseUrlField.text || "").trim(),
                singleCallTokenLimit: String(tokenLimitField.text || "").trim(),
                dailyBudgetCny: String(dailyBudgetField.text || "").trim(),
                allowUploadImages: !!allowImages.checked,
                allowUploadPdfs: !!allowPdfs.checked,
                enabledFeatures: enabledFeatures
            },
            privacy: {
                advisorMode: String(advisorChoice.currentValue || "unset"),
                allowExternalIntent: !!externalIntent.checked
            },
            autoExec: autoExec
        })
    }

    function requestCredential() {
        if (canManageCredential()) work.promptCredential(selectedProvider())
    }

    function clearCredential() {
        if (canManageCredential() && llm.hasCredential) clearDialog.open()
    }

    function submitClearCredential() {
        if (canManageCredential()) work.clearCredential(selectedProvider(), true)
        clearDialog.close()
    }

    function copyFlag(key, value) {
        markDraftDirty()
        featureStates = mapWithFlag(featureStates, key, value)
    }

    function copyAutoFlag(key, value) {
        markDraftDirty()
        autoStates = mapWithFlag(autoStates, key, value)
    }

    Component.onCompleted: { ready = true; snapshotSync.restart() }
    Connections {
        target: root.work
        enabled: root.work !== null && root.work !== undefined
        ignoreUnknownSignals: true
        function onDataChanged() { snapshotSync.restart() }
        function onStateChanged() {
            if (root.ready && root.work && !root.work.busy) snapshotSync.restart()
        }
    }

    Timer {
        id: snapshotSync
        interval: 0
        repeat: false
        onTriggered: root.syncFromSnapshot()
    }

    ScrollView {
        id: settingsScroll
        objectName: "settingsPreferencesScroll"
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        GridLayout {
            id: settingsContent
            width: Math.max(0, settingsScroll.availableWidth - 10)
            columns: 1
            rowSpacing: 8
            columnSpacing: 0

            RowLayout {
                Layout.fillWidth: true
                Layout.columnSpan: settingsContent.columns
                spacing: 5
                ActionButton {
                    objectName: "settingsAiTab"
                    text: "AI 服务"
                    primary: root.activeSection === "ai"
                    Accessible.role: Accessible.PageTab
                    Accessible.selected: root.activeSection === "ai"
                    onClicked: root.activeSection = "ai"
                }
                ActionButton {
                    objectName: "settingsPrivacyTab"
                    text: "隐私与策略"
                    primary: root.activeSection === "privacy"
                    Accessible.role: Accessible.PageTab
                    Accessible.selected: root.activeSection === "privacy"
                    onClicked: root.activeSection = "privacy"
                }
                ActionButton {
                    objectName: "settingsDataTab"
                    text: "数据维护"
                    primary: root.activeSection === "data"
                    Accessible.role: Accessible.PageTab
                    Accessible.selected: root.activeSection === "data"
                    onClicked: root.activeSection = "data"
                }
                Item { Layout.fillWidth: true }
            }

            PaperCard {
                objectName: "settingsLlmCard"
                visible: root.activeSection === "ai"
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                padding: 11

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    BodyText { text: "智能服务"; font.pixelSize: 20; font.weight: Font.DemiBold }
                    BodyText {
                        Layout.fillWidth: true
                        visible: false; text: ""
                        color: Theme.muted
                        font.pixelSize: Theme.captionFontSize
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        CheckBox {
                            id: llmEnabled
                            objectName: "settingsLlmEnabled"
                            onToggled: root.markDraftDirty()
                            text: "启用云端 LLM 增强"
                            Accessible.name: text
                            enabled: !root.busy
                        }
                        BodyText { text: "服务商"; font.pixelSize: Theme.captionFontSize; color: Theme.muted }
                        FormChoice {
                            id: providerChoice
                            objectName: "settingsProviderChoice"
                            onActivated: root.markDraftDirty()
                            onCountChanged: if (root.ready) snapshotSync.restart()
                            Layout.minimumWidth: 190
                            Layout.maximumWidth: 340
                            accessibleLabel: "选择智能服务提供方"
                            model: root.list(root.llm.providers)
                            textRole: "label"
                            valueRole: "key"
                            enabled: !root.busy
                            Layout.fillWidth: true
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3
                            BodyText { text: "模型名称"; font.pixelSize: Theme.captionFontSize; color: Theme.muted }
                            FormField {
                                id: modelField
                                objectName: "settingsModelField"
                                onTextEdited: root.markDraftDirty()
                                placeholderText: "填写模型名称"
                                accessibleLabel: "模型名称"
                                enabled: !root.busy
                                Layout.fillWidth: true
                            }
                        }
                        ColumnLayout {
                            visible: root.selectedProvider() === "custom" || root.selectedProvider() === "anthropic"
                            Layout.fillWidth: true
                            spacing: 3
                            BodyText { text: "服务接口地址"; font.pixelSize: Theme.captionFontSize; color: Theme.muted }
                            FormField {
                                id: baseUrlField
                                objectName: "settingsCustomBaseUrlField"
                                onTextEdited: root.markDraftDirty()
                                placeholderText: "HTTPS/HTTP 地址"
                                accessibleLabel: "自定义接口地址，不要在地址中填写密钥"
                                enabled: !root.busy
                                Layout.fillWidth: true
                            }
                        }
                    }
                    Text {
                        visible: !!root.llm.baseUrlNeedsReview
                        Layout.fillWidth: true
                        text: "已隐藏原接口地址中的账号、敏感路径、查询参数或片段。请重新输入不含密钥的 HTTPS/HTTP 地址后保存。"
                        textFormat: Text.PlainText
                        color: Theme.warning
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.captionFontSize
                        wrapMode: Text.Wrap
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3
                            BodyText { text: "单次 Token 上限"; font.pixelSize: Theme.captionFontSize; color: Theme.muted }
                            FormField {
                                id: tokenLimitField
                                objectName: "settingsTokenLimitField"
                                onTextEdited: root.markDraftDirty()
                                placeholderText: "1000–1000000"
                                accessibleLabel: "单次 token 上限"
                                enabled: !root.busy
                                Layout.fillWidth: true
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3
                            BodyText { text: "每日预算（元）"; font.pixelSize: Theme.captionFontSize; color: Theme.muted }
                            FormField {
                                id: dailyBudgetField
                                objectName: "settingsDailyBudgetField"
                                onTextEdited: root.markDraftDirty()
                                placeholderText: "金额上限"
                                accessibleLabel: "每日预算上限，单位元"
                                enabled: !root.busy
                                Layout.fillWidth: true
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 16
                        CheckBox {
                            id: allowImages
                            objectName: "settingsAllowImages"
                            onToggled: root.markDraftDirty()
                            text: "允许上传图片"
                            Accessible.name: text
                            enabled: !root.busy
                        }
                        CheckBox {
                            id: allowPdfs
                            objectName: "settingsAllowPdfs"
                            onToggled: root.markDraftDirty()
                            text: "允许上传 PDF"
                            Accessible.name: text
                            enabled: !root.busy
                        }
                    }

                    BodyText { text: "云端增强功能"; font.weight: Font.DemiBold }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 8
                        Repeater {
                            id: featureRepeater
                            model: root.list(root.llm.features)
                            delegate: CheckBox {
                                id: featureControl
                                required property var modelData
                                property string featureKey: String(modelData.key)
                                objectName: "settingsFeature_" + featureKey
                                text: String(modelData.label)
                                Accessible.name: text
                                checked: root.featureStates[featureKey] === true
                                enabled: !root.busy
                                onToggled: if (!root.syncing) root.copyFlag(featureKey, checked)
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        BodyText {
                            objectName: "settingsCredentialStatus"
                            Layout.fillWidth: true
                            text: !root.credentialIdentitySaved() ? "服务商或接口已修改；请先保存，再重新配置凭据。" :
                                  "凭据状态：" + (root.llm.hasCredential ? "已配置" :
                                  root.llm.credentialNeedsReview ? "需要重新配置" : "尚未配置")
                            color: root.credentialIdentitySaved() && root.llm.hasCredential ? Theme.leaf : Theme.muted
                        }
                        ActionButton {
                            objectName: "settingsCredentialButton"
                            text: "配置凭据…"
                            accessibleLabel: "使用系统安全输入框配置服务商凭据"
                            enabled: root.canManageCredential()
                            onClicked: root.requestCredential()
                        }
                        ActionButton {
                            objectName: "settingsClearCredentialButton"
                            text: "清除"
                            accessibleLabel: "清除当前服务商已保存凭据"
                            visible: !!root.llm.hasCredential
                            enabled: root.canManageCredential()
                            onClicked: root.clearCredential()
                        }
                    }
                    BodyText {
                        Layout.fillWidth: true
                        text: root.display(root.llm.status, "等待设置数据")
                        color: Theme.muted
                    }
                }
            }

            PaperCard {
                objectName: "settingsPrivacyCard"
                visible: root.activeSection === "privacy"
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                padding: 11
                paperColor: Theme.lavender
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    BodyText { text: "学习助理隐私与自动执行"; font.pixelSize: 20; font.weight: Font.DemiBold }
                    BodyText { visible: false; text: ""; color: Theme.muted; font.pixelSize: Theme.captionFontSize }
                    FormChoice {
                        id: advisorChoice
                        objectName: "settingsAdvisorModeChoice"
                        onActivated: root.markDraftDirty()
                        onCountChanged: if (root.ready) snapshotSync.restart()
                        accessibleLabel: "学习助理顾问模式"
                        model: root.list(root.privacy.advisorModes)
                        textRole: "label"
                        valueRole: "key"
                        enabled: !root.busy
                    }
                    CheckBox {
                        id: externalIntent
                        objectName: "settingsAllowExternalIntent"
                        onToggled: root.markDraftDirty()
                        text: "允许向外部顾问发送已复核的最少意图信息"
                        Accessible.name: text
                        enabled: !root.busy
                    }
                    BodyText { text: "自动执行"; font.weight: Font.DemiBold }
                    Repeater {
                        id: autoRepeater
                        model: root.list(root.settingsData.autoExecOptions)
                        delegate: CheckBox {
                            required property var modelData
                            property string actionKey: String(modelData.key)
                            objectName: "settingsAutoExec_" + actionKey
                            text: "自动执行：" + String(modelData.label)
                            Accessible.name: text
                            checked: root.autoStates[actionKey] === true
                            enabled: !root.busy
                            onToggled: if (!root.syncing) root.copyAutoFlag(actionKey, checked)
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.columnSpan: settingsContent.columns
                visible: root.activeSection === "ai" || root.activeSection === "privacy"
                Item { Layout.fillWidth: true }
                ActionButton {
                    objectName: "settingsSaveButton"
                    text: "保存设置"
                    primary: true
                    accessibleLabel: "保存服务、隐私与自动执行设置"
                    enabled: root.work && !root.busy
                    onClicked: root.saveSettings()
                }
            }

            PaperCard {
                objectName: "settingsMaintenanceCard"
                visible: root.activeSection === "data"
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                padding: 11
                paperColor: Theme.peach
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    BodyText { text: "数据管理与备份"; font.pixelSize: 20; font.weight: Font.DemiBold }
                    BodyText {
                        Layout.fillWidth: true
                        text: "备份目录：" + root.display(root.maintenance.backupRoot, "未配置")
                        color: Theme.muted
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 8
                        ActionButton {
                            objectName: "settingsBackupButton"
                            text: "立即备份"
                            primary: true
                            accessibleLabel: "创建本地数据库与学习记录备份"
                            enabled: !root.busy
                            onClicked: root.work.run("settings.backup", {})
                        }
                        ActionButton {
                            objectName: "settingsIntegrityButton"
                            text: "检查数据库"
                            accessibleLabel: "检查 SQLite、外键与文本完整性"
                            enabled: !root.busy
                            onClicked: root.work.run("settings.integrity", {})
                        }
                        ActionButton {
                            objectName: "settingsOpenBackupFolder"
                            text: "打开备份目录"
                            accessibleLabel: "在文件管理器中打开本地备份目录"
                            enabled: !root.busy
                            onClicked: root.work.openBackupFolder()
                        }
                    }
                    BodyText {
                        visible: root.maintenance.lastBackup !== null && root.maintenance.lastBackup !== undefined
                        text: "最近备份：" + root.display(root.maintenance.lastBackup && root.maintenance.lastBackup.archive, "已创建")
                        color: Theme.leaf
                    }
                    BodyText {
                        visible: root.maintenance.lastIntegrity !== null && root.maintenance.lastIntegrity !== undefined
                        text: root.display(root.maintenance.lastIntegrity && root.maintenance.lastIntegrity.summary, "")
                        color: root.maintenance.lastIntegrity && root.maintenance.lastIntegrity.healthy ? Theme.leaf : Theme.warning
                    }
                    BodyText {
                        visible: root.work && root.work.receipt && !!root.work.receipt.archive
                        text: "已创建备份：" + root.display(root.work && root.work.receipt && root.work.receipt.archive, "")
                        color: Theme.leaf
                    }
                    BodyText {
                        visible: root.work && root.work.receipt && root.work.receipt.healthy !== undefined
                        text: root.display(root.work && root.work.receipt && root.work.receipt.summary, "")
                        color: root.work && root.work.receipt && root.work.receipt.healthy ? Theme.leaf : Theme.warning
                    }
                }
            }

            PaperCard {
                objectName: "settingsAuditCard"
                visible: root.activeSection === "data"
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                padding: 10
                paperColor: Theme.pinkSoft
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 5
                    CheckBox {
                        id: auditDisclosure
                        objectName: "settingsAuditDisclosure"
                        text: "近期调用记录（" + root.audits.length + "）"
                        Accessible.name: "展开近期智能服务调用记录"
                    }
                    BodyText {
                        visible: auditDisclosure.checked && root.audits.length === 0
                        text: "暂无 LLM 调用记录。"
                        color: Theme.muted
                    }
                    ColumnLayout {
                        visible: auditDisclosure.checked
                        Layout.fillWidth: true
                        Repeater {
                            model: root.audits
                            delegate: ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 2
                                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
                                BodyText {
                                    text: root.display(modelData.createdAt, "") + " · " +
                                          root.display(modelData.featureLabel, "智能服务") + " · " +
                                          root.display(modelData.providerLabel, "") + " / " +
                                          root.display(modelData.model, "") + " · " + root.stateLabel(modelData.status)
                                    font.weight: Font.DemiBold
                                }
                                BodyText {
                                    text: "估算 " + String(modelData.estimatedTokens || 0) + " tokens"
                                    color: Theme.muted
                                }
                            }
                        }
                    }
                }
            }

            Item { Layout.preferredHeight: 8; Layout.columnSpan: settingsContent.columns }
        }
    }

    Dialog {
        id: clearDialog
        objectName: "settingsClearCredentialDialog"
        modal: true
        title: "清除已保存凭据"
        width: Math.min(430, Math.max(300, root.width - 32))
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
            BodyText {
                text: "这会删除当前服务商在本机保存的凭据。之后如需使用云端服务，需要再次通过系统安全输入框配置。"
                color: Theme.ink
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                ActionButton {
                    objectName: "settingsClearCredentialCancel"
                    text: "返回"
                    onClicked: clearDialog.close()
                }
                ActionButton {
                    objectName: "settingsClearCredentialConfirm"
                    text: "确认清除"
                    primary: true
                    onClicked: root.submitClearCredential()
                }
            }
        }
    }
}
