#include <cstddef>

void Teardown(char* left, char* right)
{
    delete left;
    delete right;
}
