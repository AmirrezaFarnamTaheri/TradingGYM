import { createContext, useContext, useEffect, useMemo, useState } from "react";
import fa from "./locales/fa.json";

const LanguageContext = createContext(null);
let activeLanguage = "en";
export function currentLocale() {
  return activeLanguage === "fa" ? "fa-IR" : "en-US";
}
export function translate(text) {
  return activeLanguage === "fa" ? fa[text] || text : text;
}
export function useT() {
  return useContext(LanguageContext)?.t || translate;
}
export function useLanguage() {
  return useContext(LanguageContext);
}
export function LanguageProvider({ children }) {
  const [language, setLanguage] = useState(() => {
    try {
      return localStorage.getItem("cockpit-language") === "fa" ? "fa" : "en";
    } catch {
      return "en";
    }
  });
  activeLanguage = language;
  useEffect(() => {
    document.documentElement.lang = language;
    document.documentElement.dir = language === "fa" ? "rtl" : "ltr";
    try {
      localStorage.setItem("cockpit-language", language);
    } catch {}
  }, [language]);
  const value = useMemo(
    () => ({ language, setLanguage, t: translate }),
    [language],
  );
  return (
    <LanguageContext.Provider value={value}>
      {children}
    </LanguageContext.Provider>
  );
}
