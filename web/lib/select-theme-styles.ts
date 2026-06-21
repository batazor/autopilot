import type { StylesConfig } from "react-select";
import type { SelectOption } from "@/components/AppSelect";

/** react-select colors follow CSS variables set in globals.css per theme. */
export const selectThemeStyles: StylesConfig<SelectOption, false> = {
  control: (base, state) => ({
    ...base,
    minHeight: 34,
    borderRadius: 8,
    borderColor: state.isFocused
      ? "var(--wos-accent-focus)"
      : "var(--wos-border)",
    backgroundColor: "var(--wos-input)",
    boxShadow: state.isFocused ? "var(--wos-accent-shadow)" : "none",
    ":hover": {
      borderColor: state.isFocused
        ? "var(--wos-accent-focus)"
        : "var(--wos-border-hover)",
    },
  }),
  valueContainer: (base) => ({
    ...base,
    padding: "0 8px",
  }),
  input: (base) => ({
    ...base,
    color: "var(--wos-text)",
  }),
  singleValue: (base) => ({
    ...base,
    color: "var(--wos-text)",
  }),
  placeholder: (base) => ({
    ...base,
    color: "var(--wos-text-muted)",
  }),
  menu: (base) => ({
    ...base,
    zIndex: 30,
    border: "1px solid var(--wos-border)",
    backgroundColor: "var(--wos-panel)",
    overflow: "hidden",
  }),
  menuList: (base) => ({
    ...base,
    padding: 4,
  }),
  option: (base, state) => ({
    ...base,
    borderRadius: 6,
    backgroundColor: state.isSelected
      ? "var(--wos-accent)"
      : state.isFocused
        ? "var(--wos-option-hover)"
        : "transparent",
    color: state.isSelected ? "var(--wos-on-accent)" : "var(--wos-text)",
    cursor: "pointer",
    ":active": {
      backgroundColor: state.isSelected
        ? "var(--wos-accent)"
        : "var(--wos-option-active)",
    },
  }),
  dropdownIndicator: (base, state) => ({
    ...base,
    color: state.isFocused ? "var(--wos-accent-muted)" : "var(--wos-text-muted)",
    ":hover": {
      color: "var(--wos-accent-muted)",
    },
  }),
  clearIndicator: (base) => ({
    ...base,
    color: "var(--wos-text-muted)",
    ":hover": {
      color: "var(--wos-text)",
    },
  }),
  indicatorSeparator: (base) => ({
    ...base,
    backgroundColor: "var(--wos-border)",
  }),
  noOptionsMessage: (base) => ({
    ...base,
    color: "var(--wos-text-muted)",
  }),
};
