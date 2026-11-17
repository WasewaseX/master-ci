#include <iostream>

void OnOpen(const char* name)
{
    std::cout << "opening " << name << "\n";
}

void OnClose(const char* name)
{
    std::cout << "closing " << name << "\n";
}
