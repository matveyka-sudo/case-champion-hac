export const accessNumber = new Intl.NumberFormat("ru", { maximumFractionDigits: 1 });
export function accessAssessment(value: number | null) {
  if (value == null) return { text: "Недостаточно данных для общей оценки", color: "var(--text-3)" };
  if (value >= 80) return { text: "Высокая доступность — до нужных объектов удобно добираться", color: "#26725a" };
  if (value >= 60) return { text: "Хорошая доступность, отдельные объекты могут быть дальше", color: "#587130" };
  if (value >= 40) return { text: "Средняя доступность — сравните время до нужных объектов", color: "#93651f" };
  return { text: "Низкая доступность — путь до нужных объектов может быть долгим", color: "#a33c55" };
}
export const accessIcons: Record<string, string> = { hospital: "hospital", school: "school", kindergarten: "baby", college: "graduation-cap", university: "graduation-cap", metro: "train-front", mcd: "train-front", mck: "train-front", mall: "store", bus_stop: "bus" };

export const accessCategoryColors: Record<string, string> = {
  hospital: "#88b9ad", school: "#d89dbb", kindergarten: "#ddb18f", college: "#a99bcc", university: "#97a9d0",
  metro: "#98b7d2", mcd: "#b7bc8c", mck: "#bc9bbf", mall: "#cea589", bus_stop: "#91b9bf",
};
