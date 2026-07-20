# Extractable components

## ApplicationSidebar

- Source: `web/index.html`
- Category: layout
- Description: Product identity, primary tab navigation, and local-first footer.
- Extractable props: `activeTab`
- Hardcoded: product mark, navigation labels, version badges, colors.

## TopStatusBar

- Source: `web/index.html`
- Category: layout
- Description: Workflow title and provider/profile/storage/model status.
- Extractable props: status values
- Hardcoded: status categories and shell structure.

## AsrModeSelector

- Source: `web/index.html` after this implementation
- Category: basic
- Description: Shared automatic, fixed-language, and multilingual choice cards.
- Extractable props: `name`, `selectedMode`, `fixedLanguage`
- Hardcoded: three supported modes, Chinese labels, explanatory copy.
