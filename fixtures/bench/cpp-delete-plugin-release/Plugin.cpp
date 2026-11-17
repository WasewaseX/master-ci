#include <cstddef>

void Release(void* handle)
{
    char* view = static_cast<char*>(handle);
    delete view;
}
