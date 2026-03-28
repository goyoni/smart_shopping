export type Locale = "en" | "he" | "ar";

// RTL locales — derived from language direction metadata.
// When adding a new RTL language, add it here and to the translations below.
const RTL_LOCALE_SET: ReadonlySet<Locale> = new Set<Locale>(["he", "ar"]);

export function isRtl(locale: Locale): boolean {
  return RTL_LOCALE_SET.has(locale);
}

export function detectLocale(): Locale {
  if (typeof navigator === "undefined") return "en";
  const lang = navigator.language?.split("-")[0];
  if (lang === "he") return "he";
  if (lang === "ar") return "ar";
  return "en";
}

const translations: Record<Locale, Record<string, string>> = {
  en: {
    "app.title": "Smart Shopping Agent",
    "nav.search": "Search",
    "nav.shopping_list": "Shopping List",
    "search.placeholder": "What are you looking for?",
    "search.button": "Search",
    "search.searching": "Searching...",
    "search.recent": "Recent searches",
    "search.results_count": "Found {count} product(s) from {sites} site(s)",
    "list.empty": "Your shopping list is empty. Add products from search results.",
    "list.loading": "Loading...",
    "product.add_to_list": "+ Add to list",
    "product.hide_sellers": "Hide sellers",
    "product.sellers_count": "Available from {count} seller(s)",
    "product.price_unavailable": "Price unavailable",
    "product.show_less": "Show less",
    "product.more_criteria": "+{count} more",
    "seller.contact_whatsapp": "Contact via WhatsApp",
    "seller.contact_email": "Contact via Email",
    "seller.visit": "Visit seller",
    "status.just_now": "just now",
    "status.minutes_ago": "{n}m ago",
    "status.hours_ago": "{n}h ago",
    "status.days_ago": "{n}d ago",
  },
  he: {
    "app.title": "סוכן קניות חכם",
    "nav.search": "חיפוש",
    "nav.shopping_list": "רשימת קניות",
    "search.placeholder": "מה אתם מחפשים?",
    "search.button": "חפש",
    "search.searching": "מחפש...",
    "search.recent": "חיפושים אחרונים",
    "search.results_count": "נמצאו {count} מוצרים מ-{sites} אתרים",
    "list.empty": "רשימת הקניות ריקה. הוסיפו מוצרים מתוצאות החיפוש.",
    "list.loading": "טוען...",
    "product.add_to_list": "+ הוסף לרשימה",
    "product.hide_sellers": "הסתר מוכרים",
    "product.sellers_count": "זמין ב-{count} מוכרים",
    "product.price_unavailable": "מחיר לא זמין",
    "product.show_less": "הצג פחות",
    "product.more_criteria": "+{count} נוספים",
    "seller.contact_whatsapp": "צור קשר בוואטסאפ",
    "seller.contact_email": 'צור קשר באימייל',
    "seller.visit": "בקר באתר המוכר",
    "status.just_now": "עכשיו",
    "status.minutes_ago": "לפני {n} דקות",
    "status.hours_ago": "לפני {n} שעות",
    "status.days_ago": "לפני {n} ימים",
  },
  ar: {
    "app.title": "وكيل التسوق الذكي",
    "nav.search": "بحث",
    "nav.shopping_list": "قائمة التسوق",
    "search.placeholder": "ما الذي تبحث عنه؟",
    "search.button": "بحث",
    "search.searching": "جارٍ البحث...",
    "search.recent": "عمليات بحث حديثة",
    "search.results_count": "تم العثور على {count} منتج من {sites} مواقع",
    "list.empty": "قائمة التسوق فارغة. أضف منتجات من نتائج البحث.",
    "list.loading": "جارٍ التحميل...",
    "product.add_to_list": "+ أضف إلى القائمة",
    "product.hide_sellers": "إخفاء البائعين",
    "product.sellers_count": "متوفر من {count} بائعين",
    "product.price_unavailable": "السعر غير متوفر",
    "product.show_less": "عرض أقل",
    "product.more_criteria": "+{count} المزيد",
    "seller.contact_whatsapp": "تواصل عبر واتساب",
    "seller.contact_email": "تواصل عبر البريد الإلكتروني",
    "seller.visit": "زيارة البائع",
    "status.just_now": "الآن",
    "status.minutes_ago": "منذ {n} دقائق",
    "status.hours_ago": "منذ {n} ساعات",
    "status.days_ago": "منذ {n} أيام",
  },
};

export function t(
  locale: Locale,
  key: string,
  params?: Record<string, string | number>,
): string {
  let text = translations[locale]?.[key] || translations.en[key] || key;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      text = text.replace(`{${k}}`, String(v));
    }
  }
  return text;
}
