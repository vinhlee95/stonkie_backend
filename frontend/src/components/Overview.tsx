import React from 'react';
import { Box, Typography } from '@mui/material';
import { FinancialData, ReportType } from '../types';
import { Chart } from 'react-chartjs-2';

interface OverviewProps {
  financialData: Record<ReportType, FinancialData | null>;
}

const Overview: React.FC<OverviewProps> = ({ financialData }) => {
  const renderFinancialChart = (data: FinancialData | null) => {
    if (!data) return null;
    const columns = data.columns
    
    const revenueRow = data.data.find(row => {
      const metric = row[columns[0]];
      return typeof metric === 'string' && 
        metric.toLowerCase().includes('revenue') && 
        !metric.toLowerCase().includes('cost');
    });
    const netIncome = data.data.find(row => {
      const metric = row[columns[0]];
      return typeof metric === 'string' && 
        metric.toLowerCase().includes('net income');
    });

    // Reverse the columns array (except the first column which contains metrics)
    const reversedColumns = [data.columns[0], ...data.columns.slice(1).reverse()];
    
    // Update the data rows to match the reversed column order
    const reversedData = data.data.map(row => {
      const rowValues = Object.values(row);
      return [rowValues[0], ...rowValues.slice(1).reverse()];
    });

    // Update the data object with reversed columns and data
    data = {
      ...data,
      columns: reversedColumns,
      data: reversedData.map(row => {
        const obj: Record<string, string | number> = {};
        reversedColumns.forEach((col, i) => {
          obj[col] = row[i];
        });
        return obj;
      })
    };

    if (!revenueRow || !netIncome) return null;

    const years = data.columns.slice(1);
    
    // Calculate net margin with 2 decimal places
    const netMarginData = years.map(year => {
      const revenue = parseFloat(revenueRow[year].toString().replace(/[^0-9.-]+/g, ''));
      const income = parseFloat(netIncome[year].toString().replace(/[^0-9.-]+/g, ''));
      return Number(((income / revenue) * 100).toFixed(2)); // Round to 2 decimal places
    });

    const chartData = {
      labels: years,
      datasets: [
        {
          type: 'bar' as const,
          label: 'Revenue',
          data: years.map(year => parseFloat(revenueRow[year].toString().replace(/[^0-9.-]+/g, ''))),
          backgroundColor: 'rgba(53, 162, 235, 0.5)',
          borderColor: 'rgba(53, 162, 235, 1)',
          borderWidth: 1,
          yAxisID: 'y',
        },
        {
          type: 'bar' as const,
          label: 'Net income',
          data: years.map(year => parseFloat(netIncome[year].toString().replace(/[^0-9.-]+/g, ''))),
          backgroundColor: 'rgba(75, 192, 192, 0.5)',
          borderColor: 'rgba(75, 192, 192, 1)',
          borderWidth: 1,
          yAxisID: 'y',
        },
        {
          type: 'line' as const,
          label: 'Net Margin (%)',
          data: netMarginData,
          borderColor: 'rgba(255, 159, 64, 1)',
          borderWidth: 2,
          pointBackgroundColor: 'rgba(255, 159, 64, 1)',
          pointRadius: 4,
          fill: false,
          yAxisID: 'percentage',
        },
      ],
    };

    const options = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'top' as const,
        },
        title: {
          display: true,
          text: 'Revenue, Net Income, and Net Margin Trends',
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          position: 'left' as const,
          ticks: {
            callback: function(value: any, index: number, values: any[]): string {
              if (typeof value !== 'number') return '';
              return value >= 1e9 
                ? `$${(value / 1e9).toFixed(1)}B`
                : value >= 1e6
                ? `$${(value / 1e6).toFixed(1)}M`
                : `$${value}`;
            },
          },
        },
        percentage: {
          beginAtZero: true,
          position: 'right' as const,
          grid: {
            drawOnChartArea: false,
          },
          ticks: {
            callback: function(value: any): string {
              return `${Number(value).toFixed(2)}%`;
            },
          },
        },
      },
    };

    return (
      <Box sx={{ height: 400, mt: 4 }}>
        <Typography variant="h5" sx={{ mb: 2 }}>
          Growth and profitability
        </Typography>
        <Chart type='bar' data={chartData} options={options} />
      </Box>
    );
  };

  return (
    <>
      {renderFinancialChart(financialData.income_statement)}
    </>
  );
};

export default Overview; 