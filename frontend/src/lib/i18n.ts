export type Locale = "ar" | "en";

export const LOCALES: Locale[] = ["ar", "en"];
export const DEFAULT_LOCALE: Locale = "ar";

export const dirFor = (locale: Locale) => (locale === "ar" ? "rtl" : "ltr");

const STRINGS = {
  ar: {
    appName: "قارئ المستندات",
    library: "المكتبة",
    upload: "رفع مستند",
    chat: "محادثة",
    settings: "الإعدادات",
    dropHere: "اسحب ملفاً هنا أو اضغط للاختيار",
    supportedFiles: "PDF أو صورة — عربي و/أو إنجليزي",
    selectEngines: "اختر محركات التعرف الضوئي",
    engineHint: "اختر محركاً واحداً أو عدة محركات لمقارنة النتائج",
    runOcr: "ابدأ التعرف الضوئي",
    processing: "جارٍ معالجة المستند…",
    page: "صفحة",
    of: "من",
    comparison: "مقارنة النتائج",
    selectThis: "اختر هذه النتيجة",
    editText: "تحرير النص",
    save: "حفظ",
    finalize: "اعتماد النص",
    aiImprove: "تحسين بالذكاء الاصطناعي",
    metadata: "البيانات الوصفية",
    generateMetadata: "استخراج البيانات الوصفية",
    chatWithDoc: "حادث هذا المستند",
    askAnything: "اسأل عن مستنداتك…",
    send: "إرسال",
    sources: "المصادر",
    unavailable: "غير متاح",
    confidence: "الثقة",
    duration: "المدة",
    characters: "عدد الأحرف",
    searching: "أبحث في مستنداتك…",
    noDocuments: "لا توجد مستندات بعد",
    status: "الحالة",
  },
  en: {
    appName: "Document Reader",
    library: "Library",
    upload: "Upload",
    chat: "Chat",
    settings: "Settings",
    dropHere: "Drop a file here, or click to choose",
    supportedFiles: "PDF or image — Arabic and/or English",
    selectEngines: "Choose OCR engines",
    engineHint: "Pick one, or several to compare their results",
    runOcr: "Run OCR",
    processing: "Processing document…",
    page: "Page",
    of: "of",
    comparison: "Compare results",
    selectThis: "Use this result",
    editText: "Edit text",
    save: "Save",
    finalize: "Finalize text",
    aiImprove: "Improve with AI",
    metadata: "Metadata",
    generateMetadata: "Extract metadata",
    chatWithDoc: "Chat with this document",
    askAnything: "Ask about your documents…",
    send: "Send",
    sources: "Sources",
    unavailable: "Unavailable",
    confidence: "Confidence",
    duration: "Time",
    characters: "Characters",
    searching: "Searching your documents…",
    noDocuments: "No documents yet",
    status: "Status",
  },
} as const;

export type StringKey = keyof (typeof STRINGS)["en"];

export const t = (locale: Locale, key: StringKey): string =>
  STRINGS[locale][key] ?? STRINGS.en[key] ?? key;

/**
 * Force Latin digits. Arabic-Indic digits in a comparison table or a confidence
 * bar make correct OCR look wrong, so every number in chrome renders Latin.
 */
export const num = (value: number, digits = 0): string =>
  new Intl.NumberFormat("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
