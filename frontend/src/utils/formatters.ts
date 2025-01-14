export const formatNumber = (value: string | number | null | undefined): string => {
  // Handle null, undefined, empty values
  if (value === null || value === undefined || value === '') return 'N/A';
  if (value === 'N/A') return value;
  
  // Convert to number and check if it's valid
  const num = typeof value === 'string' ? parseFloat(value) : value;
  if (isNaN(num)) return value.toString();
  
  // Format with commas
  return num.toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0
  });
}; 