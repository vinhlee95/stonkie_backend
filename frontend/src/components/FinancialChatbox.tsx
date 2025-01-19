import React, { useState, useEffect } from 'react';
import { Box, TextField, Button, Paper, Typography, CircularProgress, InputAdornment } from '@mui/material';
import ChatBubbleIcon from '@mui/icons-material/ChatBubble';
import SendIcon from '@mui/icons-material/Send';
import ReactMarkdown from 'react-markdown';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';

interface Message {
  type: 'user' | 'bot';
  content: string;
  isFAQ?: boolean;  // Add this field to distinguish FAQ messages
  suggestions?: string[];  // Add this field for FAQ suggestions
}

interface FinancialChatboxProps {
  ticker: string;  // Current ticker passed from parent component
  initialMessage: string;  // Add this new prop
}

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || 'http://localhost:8080'

const FinancialChatbox: React.FC<FinancialChatboxProps> = ({ ticker, initialMessage }) => {
  const [messages, setMessages] = useState<Message[]>(() => [
    // Initialize messages with the welcome message and FAQs
    { 
      type: 'bot', 
      content: initialMessage 
    },
    {
      type: 'bot',
      content: "Here are some general frequently asked questions:",
      isFAQ: true,
      suggestions: [
        "What is a company's total asset?",
        "How is profit margin calculated?",
        "Where can I find a company's profit margin?"
      ]
    }
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isVisible, setIsVisible] = useState(false);
  const [showScrollButton, setShowScrollButton] = useState(false);
  const messagesEndRef = React.useRef<HTMLDivElement>(null);

  const handleFAQClick = async (question: string) => {
    setInput(question);
    // Simulate form submission with the selected question
    const fakeEvent = { preventDefault: () => {} } as React.FormEvent;
    await handleSubmit(fakeEvent, question);
  };

  const handleSubmit = async (e: React.FormEvent, forcedInput?: string) => {
    e.preventDefault();
    const questionToAsk = forcedInput || input;
    if (!questionToAsk.trim()) return;

    // Add user message to chat
    const userMessage: Message = { type: 'user', content: questionToAsk };
    setMessages(prev => [...prev, userMessage]);
    setIsLoading(true);

    try {
      const response = await fetch(`${BACKEND_URL}/api/company/analyze`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ question: questionToAsk, ticker: ticker }),
      });

      if (!response.ok) {
        throw new Error('Failed to get analysis');
      }

      const responseData = await response.json();
      
      // Add bot response to chat - now accessing nested data structure
      const botMessage: Message = { 
        type: 'bot', 
        content: responseData.data.data || responseData.data 
      };
      setMessages(prev => [...prev, botMessage]);
    } catch (error) {
      // Add error message to chat
      const errorMessage: Message = { 
        type: 'bot', 
        content: 'Sorry, I encountered an error analyzing the data.' 
      };
      setMessages(prev => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
      setInput('');
    }
  };

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const element = e.currentTarget;
    const isScrollable = element.scrollHeight > element.clientHeight;
    setShowScrollButton(isScrollable);
  };

  useEffect(() => {
    const chatBox = messagesEndRef.current?.parentElement;
    if (chatBox) {
      const isScrollable = chatBox.scrollHeight > chatBox.clientHeight;
      setShowScrollButton(isScrollable);
    }
  }, [messages]);

  const MessageContent: React.FC<{ content: string, isUser: boolean, isFAQ?: boolean, suggestions?: string[] }> = 
    ({ content, isUser, isFAQ, suggestions }) => {
    if (isUser) {
      return <Typography>{content}</Typography>;
    }

    if (isFAQ && suggestions) {
      return (
        <Box>
          <Typography sx={{ mb: 1 }}>{content}</Typography>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            {suggestions.map((suggestion, index) => (
              <Button
                key={index}
                variant="outlined"
                size="small"
                onClick={() => handleFAQClick(suggestion)}
                sx={{
                  justifyContent: 'flex-start',
                  textAlign: 'left',
                  textTransform: 'none',
                  p: 1,
                  borderColor: 'grey.300',
                  color: 'text.primary',
                  '&:hover': {
                    backgroundColor: 'action.hover',
                    borderColor: 'primary.main',
                  }
                }}
              >
                {suggestion}
              </Button>
            ))}
          </Box>
        </Box>
      );
    }
    
    return (
      <ReactMarkdown
        components={{
          h2: ({ children }) => (
            <Typography variant="h6" sx={{ fontWeight: 'bold', mt: 1, mb: 2 }}>
              {children}
            </Typography>
          ),
          p: ({ children }) => (
            <Typography sx={{ mb: 1.5 }}>{children}</Typography>
          ),
          strong: ({ children }) => (
            <Typography component="span" sx={{ fontWeight: 'bold' }}>
              {children}
            </Typography>
          ),
          ul: ({ children }) => (
            <Box component="ul" sx={{ pl: 2, mb: 1.5 }}>
              {children}
            </Box>
          ),
          li: ({ children }) => (
            <Typography component="li" sx={{ mb: 0.5 }}>
              {children}
            </Typography>
          ),
          table: ({ children }) => (
            <Box sx={{ overflowX: 'auto', mb: 2 }}>
              <table style={{ 
                borderCollapse: 'collapse', 
                width: '100%',
                fontSize: '0.875rem'
              }}>
                {children}
              </table>
            </Box>
          ),
          th: ({ children }) => (
            <th style={{ 
              border: '1px solid #ddd',
              padding: '8px',
              backgroundColor: '#f5f5f5',
              textAlign: 'left'
            }}>
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td style={{ 
              border: '1px solid #ddd',
              padding: '8px'
            }}>
              {children}
            </td>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    );
  };

  return (
    <Box sx={{ 
      position: 'fixed', 
      bottom: { xs: 0, sm: 20 },  // Remove bottom margin on mobile
      right: { xs: 0, sm: 20 },
      maxWidth: { xs: '100%', sm: '90%' },
      width: { xs: '100%', sm: 600 },
      px: { xs: 0, sm: 0 }  // Remove horizontal padding on mobile
    }}>
      {!isVisible && (
        <Button 
          onClick={() => setIsVisible(true)} 
          variant="contained" 
          sx={{ 
            minWidth: 'auto',
            width: 56,
            height: 56,
            borderRadius: '50%',
            float: 'right'
          }}
        >
          <ChatBubbleIcon />
        </Button>
      )}
      
      {isVisible && (
        <Paper elevation={3} sx={{ 
          p: 2, 
          clear: 'both', 
          position: 'relative',
          borderRadius: { xs: '16px 16px 0 0', sm: 4 }  // Rounded top corners only on mobile, all corners on desktop
        }}>
          {/* Header Section */}
          <Box sx={{ 
            display: 'flex', 
            justifyContent: 'space-between', 
            alignItems: 'center',
            borderBottom: 1,
            borderColor: 'divider',
            pb: 1,
            mb: 2
          }}>
            <Typography variant="h6" sx={{ 
              display: 'flex', 
              alignItems: 'center',
              gap: 1
            }}>
              Chat with Stonkie
            </Typography>
            <Button 
              onClick={() => setIsVisible(false)}
              sx={{ 
                minWidth: 'auto',
                width: 32,
                height: 32,
                p: 0,
                borderRadius: '50%',
                '&:hover': {
                  backgroundColor: 'rgba(0, 0, 0, 0.04)'
                }
              }}
            >
              <Typography sx={{ fontSize: '20px' }}>✕</Typography>
            </Button>
          </Box>
          
          <Box sx={{ 
            height: {
              xs: '60vh',
              sm: '75vh'
            },
            overflowY: 'auto',
            mt: 2,
            position: 'relative',
            mr: -2,  // Negative margin to extend to edge on all viewports
            pr: 2,   // Padding to maintain content spacing on all viewports
            '&::-webkit-scrollbar': {
              width: '8px',
            },
            '&::-webkit-scrollbar-track': {
              background: '#f1f1f1',
            },
            '&::-webkit-scrollbar-thumb': {
              background: '#888',
              borderRadius: '4px',
            },
            '&::-webkit-scrollbar-thumb:hover': {
              background: '#555',
            },
          }}
          onScroll={handleScroll}>
            {showScrollButton && (
              <Box sx={{ 
                position: 'sticky',
                top: '1px',
                left: '1px',
                zIndex: 1,
                ml: 2,
                marginLeft: 0
              }}>
                <Button
                  onClick={scrollToBottom}
                  size="small"
                  sx={{
                    minWidth: '32px',
                    width: '32px',
                    height: '32px',
                    borderRadius: '50%',
                    padding: 0,
                    backgroundColor: 'background.paper',
                    boxShadow: 1,
                    '&:hover': {
                      backgroundColor: 'action.hover',
                    }
                  }}
                >
                  <KeyboardArrowDownIcon />
                </Button>
              </Box>
            )}
            {messages.map((message, index) => (
              <Box
                key={index}
                sx={{
                  display: 'flex',
                  justifyContent: message.type === 'user' ? 'flex-end' : 'flex-start',
                  mb: 2,
                  position: 'relative',
                  alignItems: 'flex-start',
                }}
              >
                {message.type === 'bot' && (
                  <Box
                    component="img"
                    src="/stonkie.png"
                    alt="AI Avatar"
                    sx={{
                      width: 40,
                      height: 40,
                      borderRadius: '50%',
                      mr: 1,
                      flexShrink: 0,
                    }}
                  />
                )}
                <Paper
                  sx={{
                    p: 1.5,
                    maxWidth: message.type === 'user' ? '70%' : '85%',
                    bgcolor: message.type === 'user' ? 'primary.light' : 'grey.50',
                    color: message.type === 'user' ? 'white' : 'text.primary',
                    borderRadius: 2,
                    boxShadow: 2,
                    position: 'relative',
                    '&::before': message.type === 'bot' ? {
                      content: '""',
                      position: 'absolute',
                      width: 0,
                      height: 0,
                      borderStyle: 'solid',
                      left: -10,
                      borderWidth: '10px 10px 10px 0',
                      borderColor: 'transparent #f5f5f5 transparent transparent',
                      top: 10,
                    } : {}
                  }}
                >
                  <MessageContent 
                    content={message.content} 
                    isUser={message.type === 'user'}
                    isFAQ={message.isFAQ}
                    suggestions={message.suggestions}
                  />
                </Paper>
              </Box>
            ))}
            {isLoading && (
              <Box sx={{ display: 'flex', justifyContent: 'center', mt: 2 }}>
                <CircularProgress size={24} />
              </Box>
            )}
            <div ref={messagesEndRef} />
          </Box>

          <form onSubmit={(e) => handleSubmit(e)}>
            <TextField
              fullWidth
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about financial analysis..."
              disabled={isLoading}
              size="small"
              sx={{
                '& .MuiOutlinedInput-root': {
                  borderRadius: 3,
                  pr: '8px', // Reduce right padding to accommodate button
                }
              }}
              InputProps={{
                endAdornment: (
                  <InputAdornment position="end">
                    <Button 
                      type="submit" 
                      disabled={isLoading || !input.trim()}
                      sx={{
                        minWidth: '40px',
                        width: '40px',
                        height: '40px',
                        borderRadius: '50%',
                        p: 0,
                      }}
                    >
                      <SendIcon fontSize="small" />
                    </Button>
                  </InputAdornment>
                ),
              }}
            />
          </form>
        </Paper>
      )}
    </Box>
  );
};

export default FinancialChatbox; 