#include <stdexcept>

void Seek(int offset)
{
    throw std::logic_error("seek is not implemented for this stream");
}
