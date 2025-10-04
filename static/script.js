document.addEventListener("DOMContentLoaded", () => {
    // --- ELEMENT SELECTORS ---
    const chatBox = document.getElementById("chat-box");
    const chatForm = document.getElementById("chat-form");
    const userInput = document.getElementById("user-input");
    const sendButton = chatForm.querySelector("button");
    const typingIndicator = document.getElementById("typing-indicator");
    const buttonWrapper = document.getElementById("button-container-wrapper");

    // --- STATE MANAGEMENT ---
    const conversationState = {
        scenario: null,
        awaitingArticleConfirmation: false,
        isLoading: false // Prevents multiple submissions
    };

    // --- MAIN FUNCTION TO HANDLE SENDING A MESSAGE ---
    const handleUserMessage = async (messageText, isButtonClick = false) => {
        if (!messageText || conversationState.isLoading) return;

        conversationState.isLoading = true;
        sendButton.disabled = true;
        userInput.disabled = true;
        
        if (!isButtonClick) {
            addMessageToChatbox(messageText, "user-message");
        }
        userInput.value = "";
        
        showTypingIndicator(true);

        try {
            const response = await fetch("/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: messageText, state: conversationState })
            });

            if (!response.ok) throw new Error(`HTTP error! Status: ${response.status}`);

            const data = await response.json();
            
            addMessageToChatbox(data.response_text, "bot-message");

            conversationState.scenario = data.state.scenario;
            conversationState.awaitingArticleConfirmation = data.state.awaitingArticleConfirmation;

            if (conversationState.awaitingArticleConfirmation) {
                createChoiceButtons();
            }

        } catch (error) {
            console.error("Error fetching chat response:", error);
            addMessageToChatbox("Sorry, something went wrong. Please try again.", "bot-message");
        } finally {
            showTypingIndicator(false);
            conversationState.isLoading = false;
            if (!conversationState.awaitingArticleConfirmation) {
                sendButton.disabled = false;
                userInput.disabled = false;
                userInput.focus();
            }
        }
    };

    // --- EVENT LISTENERS ---
    chatForm.addEventListener("submit", (e) => {
        e.preventDefault();
        handleUserMessage(userInput.value.trim());
    });

    const handleChoiceClick = (event) => {
        const choiceValue = event.target.dataset.value;
        const choiceText = event.target.textContent;
        buttonWrapper.innerHTML = '';
        addMessageToChatbox(choiceText, "user-message");
        handleUserMessage(choiceValue, true);
    };

    // --- HELPER FUNCTIONS ---
    function addMessageToChatbox(message, className) {
        const messageElement = document.createElement("div");
        messageElement.className = `message ${className}`;
        let html = message.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>');
        messageElement.innerHTML = `<p>${html}</p>`;
        
        // This is the key change: inserts the new message BEFORE the indicator
        chatBox.insertBefore(messageElement, typingIndicator);
        
        chatBox.scrollTop = chatBox.scrollHeight;
    }

    function showTypingIndicator(show) {
        typingIndicator.classList.toggle('hidden', !show);
        if (show) {
            chatBox.scrollTop = chatBox.scrollHeight;
        }
    }

    function createChoiceButtons() {
        buttonWrapper.innerHTML = `
            <div class="button-container">
                <button class="choice-button" data-value="yes">Yes, show articles</button>
                <button class="choice-button" data-value="no">No, thank you</button>
            </div>
        `;
        buttonWrapper.querySelectorAll('.choice-button').forEach(button => {
            button.addEventListener('click', handleChoiceClick);
        });
    }
});