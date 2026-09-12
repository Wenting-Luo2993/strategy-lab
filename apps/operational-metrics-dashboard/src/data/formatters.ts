export function formatCurrency(value: number | null | undefined, currencyCode?: string | null): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "--";
  }
  const normalizedCurrency = currencyCode?.toUpperCase();
  if (!normalizedCurrency || !/^[A-Z]{3}$/.test(normalizedCurrency) || normalizedCurrency === "BASE") {
    return `${value.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 })} (currency unavailable)`;
  }
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: normalizedCurrency,
    maximumFractionDigits: 2,
  });
}
