const OPTION_QUESTION_TYPES = new Set([
  "single_choice",
  "multiple_choice",
  "yes_no",
  "ranking",
]);

export function presenterOptions(question) {
  if (!OPTION_QUESTION_TYPES.has(question?.type) || !Array.isArray(question.options)) return [];
  return question.options.map((option, index) => ({
    id: option.id,
    label: option.label,
    marker: String.fromCharCode(65 + index),
  }));
}
