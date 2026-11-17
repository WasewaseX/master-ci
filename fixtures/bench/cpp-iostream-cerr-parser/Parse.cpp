#include <iostream>

void OnBadToken(const char* token)
{
    std::cerr << "skipped token " << token << "\n";
}
