#src/agentcy/llm_utilities/conversation_manager.py

def openai_prompt(agent_description, prompt):
    full_message = [
        {
            "role": "system",
            "content": agent_description,
        },
        {"role": "user", "content": prompt},
    ]

    return  full_message

def llama_prompt(agent_description, prompt):
    message = [
        {
           "role": "system",
           "content": agent_description
        },
        {
            "role": "user",
            "content": prompt
        }
    ]

    return message