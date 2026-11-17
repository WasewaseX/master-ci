#include <fstream>

void Stamp(std::ofstream& transcript, const char* line)
{
    transcript << line << std::endl;
}
