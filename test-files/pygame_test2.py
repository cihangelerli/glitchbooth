import pygame

pygame.display.init()

screen = pygame.display.set_mode((640,480))

while True:
    screen.fill((255,0,0))
    pygame.display.flip()

