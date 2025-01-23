import React from 'react';
import { Box, Typography, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper } from '@mui/material';
import { FinancialData, ReportType } from '../types';
import { formatNumber } from '../utils/formatters';

interface StatementsProps {
  financialData: Record<ReportType, FinancialData | null>;
}

const Statements: React.FC<StatementsProps> = ({ financialData }) => {
  const renderTable = (data: FinancialData | null, title: string) => {
    if (!data) return null;

    const formatCellValue = (value: any, column: string): string => {
      // Don't format the first column (usually metric names)
      if (column === data.columns[0]) return value;
      return formatNumber(value);
    };

    return (
      <Box sx={{ mt: 4 }}>
        <Typography variant="h5" sx={{ mb: 2 }}>
          {title}
        </Typography>
        <Typography variant="body1" sx={{ mb: 2 }}>
          All numbers are in thousands of USD.
        </Typography>
        <TableContainer component={Paper} sx={{ maxHeight: 440 }}>
          <Table stickyHeader size="small">
            <TableHead>
              <TableRow>
                {data.columns.map((column, index) => (
                  <TableCell
                    key={index}
                    align={index === 0 ? 'left' : 'right'}
                    sx={{
                      fontWeight: 'bold',
                      backgroundColor: 'background.paper'
                    }}
                  >
                    {column}
                  </TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {data.data.map((row, rowIndex) => (
                <TableRow
                  key={rowIndex}
                  sx={{ '&:nth-of-type(odd)': { backgroundColor: 'action.hover' } }}
                >
                  {data.columns.map((column, colIndex) => (
                    <TableCell 
                      key={colIndex}
                      align={colIndex === 0 ? 'left' : 'right'}
                    >
                      {formatCellValue(row[column], column)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Box>
    );
  };

  return (
    <>
      {renderTable(financialData.income_statement, 'Income Statement')}
      {renderTable(financialData.balance_sheet, 'Balance Sheet')}
      {renderTable(financialData.cash_flow, 'Cash Flow Statement')}
    </>
  );
};

export default Statements; 