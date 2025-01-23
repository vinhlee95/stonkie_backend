import React, { useState, useCallback } from 'react';
import {
  TextField,
  Button,
  Box,
  CircularProgress,
  Autocomplete,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import { debounce } from 'lodash';

interface CompanySearchProps {
  ticker: string;
  loading: boolean;
  onTickerChange: (ticker: string) => void;
  onSubmit: (e: React.FormEvent) => Promise<void>;
}

const CompanySearch: React.FC<CompanySearchProps> = ({
  ticker,
  loading,
  onTickerChange,
  onSubmit,
}) => {
  const [searchResults, setSearchResults] = useState<Array<{
    symbol: string;
    name: string;
  }>>([]);
  const [searchLoading, setSearchLoading] = useState(false);

  const searchSymbols = async (query: string) => {
    if (!query || query.length < 1) {
      setSearchResults([]);
      return;
    }

    setSearchLoading(true);
    try {
      const response = await fetch(
        `https://www.alphavantage.co/query?function=SYMBOL_SEARCH&keywords=${query}&apikey=${process.env.REACT_APP_ALPHA_VANTAGE_API_KEY}`
      );
      const data = await response.json();
      
      if (data.bestMatches) {
        setSearchResults(
          data.bestMatches.map((match: any) => ({
            symbol: match['1. symbol'],
            name: match['2. name'],
          }))
        );
      }
    } catch (err) {
      console.error('Failed to fetch symbols:', err);
    } finally {
      setSearchLoading(false);
    }
  };

  const debouncedSearch = useCallback(
    debounce((query: string) => {
      searchSymbols(query);
    }, 300),
    []
  );

  return (
    <Box sx={{ 
      display: 'flex', 
      alignItems: 'center', 
      mb: 4,
      gap: 2,
      width: '100%'
    }}>
      <Box
        component="img"
        src="/stonkie.png" 
        alt="Stonkie logo" 
        sx={{ 
          height: '60px',
          '@media (max-width: 600px)': {
            height: '40px'
          }
        }}
      />
      <Box component="form" onSubmit={onSubmit} sx={{ flexGrow: 1 }}>
        <Box sx={{ display: 'flex', gap: 2 }}>
          <Autocomplete
            value={ticker}
            onChange={(_, newValue) => onTickerChange(typeof newValue === 'string' ? newValue : newValue?.symbol || '')}
            onInputChange={(_, newInputValue, reason) => {
              if (reason === 'input') {
                debouncedSearch(newInputValue);
              }
            }}
            options={searchResults}
            getOptionLabel={(option) => 
              typeof option === 'string' 
                ? option 
                : `${option.symbol} - ${option.name}`
            }
            renderInput={(params) => (
              <TextField
                {...params}
                placeholder="Enter stock ticker (e.g., AAPL)"
                label="Stock Ticker"
                variant="outlined"
                size="small"
                sx={{ 
                  '& .MuiOutlinedInput-root': {
                    borderRadius: '12px'
                  }
                }}
                InputProps={{
                  ...params.InputProps,
                  endAdornment: (
                    <>
                      {searchLoading && (
                        <CircularProgress color="inherit" size={20} />
                      )}
                      {params.InputProps.endAdornment}
                    </>
                  ),
                }}
              />
            )}
            freeSolo
            sx={{ flexGrow: 1 }}
            loading={searchLoading}
          />
          <Button
            type="submit"
            variant="contained"
            disabled={loading || !ticker.trim()}
            sx={{ 
              minWidth: 120,
              borderRadius: '12px',
              '@media (max-width: 600px)': {
                minWidth: 42,
              }
            }}
          >
            {loading ? (
              <CircularProgress size={24} color="inherit" />
            ) : (
              <DownloadIcon />
            )}
          </Button>
        </Box>
      </Box>
    </Box>
  );
};

export default CompanySearch; 